"""Bounded image detection and normalization for coding-tool attachments."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from PIL import Image, UnidentifiedImageError

DEFAULT_MAX_IMAGE_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_SOURCE_IMAGE_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_IMAGE_DIMENSION = 2_000
DEFAULT_MAX_SOURCE_PIXELS = 40_000_000
MAX_RESIZE_ATTEMPTS = 12
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
type PngKind = Literal["static", "animated", "invalid"]


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    """Provider-safe encoded image and notes about transformations applied."""

    data: bytes
    mime_type: str
    width: int
    height: int
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ImageProcessingFailure:
    """Safe user-facing reason why an image attachment was omitted."""

    message: str


def detect_image_family_mime_type(data: bytes) -> str | None:
    """Identify a known image family from the minimum available magic bytes."""
    if data.startswith(b"\xff\xd8\xff\xf7"):
        return "image/jxl"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(PNG_SIGNATURE):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return None


def unsupported_image_reason(data: bytes) -> str | None:
    """Explain recognized image variants that Tau intentionally cannot attach."""
    if data.startswith(b"\xff\xd8\xff\xf7"):
        return "JPEG XL images are not supported"
    if data.startswith(PNG_SIGNATURE) and _classify_png(data) == "animated":
        return "animated PNG images are not supported"
    return None


def detect_supported_image_mime_type(data: bytes) -> str | None:
    """Detect a supported image from its bytes while rejecting unsafe variants."""
    if data.startswith(b"\xff\xd8\xff"):
        return None if data.startswith(b"\xff\xd8\xff\xf7") else "image/jpeg"
    if data.startswith(PNG_SIGNATURE):
        return "image/png" if _classify_png(data) == "static" else None
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if (
        data.startswith(b"RIFF")
        and len(data) >= 16
        and data[8:12] == b"WEBP"
        and data[12:16] in {b"VP8 ", b"VP8L", b"VP8X"}
    ):
        return "image/webp"
    if data.startswith(b"BM") and _is_valid_bmp_header(data):
        return "image/bmp"
    return None


def process_image(
    data: bytes,
    mime_type: str,
    *,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    max_dimension: int = DEFAULT_MAX_IMAGE_DIMENSION,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_IMAGE_BYTES,
    max_source_pixels: int = DEFAULT_MAX_SOURCE_PIXELS,
) -> ProcessedImage | ImageProcessingFailure:
    """Validate and, when needed, normalize an image within deterministic limits."""
    if len(data) > max_source_bytes:
        return ImageProcessingFailure(
            f"source is {_format_size(len(data))}, exceeding the "
            f"{_format_size(max_source_bytes)} processing limit"
        )

    try:
        width, height, animated = _validated_image_metadata(data)
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as error:
        return ImageProcessingFailure(f"could not decode a valid image ({error})")

    if width * height > max_source_pixels:
        return ImageProcessingFailure(
            f"image has {width * height:,} pixels, exceeding the {max_source_pixels:,}-pixel "
            "processing limit"
        )

    requires_conversion = mime_type == "image/bmp"
    requires_resize = width > max_dimension or height > max_dimension or len(data) > max_bytes
    if not requires_conversion and not requires_resize:
        return ProcessedImage(data=data, mime_type=mime_type, width=width, height=height)

    if animated:
        return ImageProcessingFailure(
            "animated image exceeds inline limits and cannot be resized without losing animation"
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as opened:
                opened.load()
                image = opened.copy()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        ValueError,
    ) as error:
        return ImageProcessingFailure(f"could not decode image pixels ({error})")

    output_mime = "image/png" if requires_conversion else mime_type
    notes: list[str] = []
    if output_mime != mime_type:
        notes.append(f"Image converted from {mime_type} to {output_mime}.")

    target_width, target_height = _bounded_dimensions(width, height, max_dimension)
    if (target_width, target_height) != (width, height):
        image.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)

    for attempt in range(MAX_RESIZE_ATTEMPTS):
        try:
            encoded = _encode_image(image, output_mime, attempt)
        except Exception as error:
            return ImageProcessingFailure(f"could not encode processed image ({error})")
        if len(encoded) <= max_bytes:
            final_width, final_height = image.size
            if (final_width, final_height) != (width, height):
                notes.append(
                    f"Image resized from {width}x{height} to {final_width}x{final_height}."
                )
            return ProcessedImage(
                data=encoded,
                mime_type=output_mime,
                width=final_width,
                height=final_height,
                notes=tuple(notes),
            )

        next_width = max(1, int(image.width * 0.8))
        next_height = max(1, int(image.height * 0.8))
        if (next_width, next_height) == image.size:
            break
        image = image.resize((next_width, next_height), Image.Resampling.LANCZOS)

    return ImageProcessingFailure(
        f"could not resize image below the {_format_size(max_bytes)} attachment limit"
    )


def _validated_image_metadata(data: bytes) -> tuple[int, int, bool]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            animated = bool(getattr(image, "is_animated", False))
            image.verify()
    if width < 1 or height < 1:
        raise ValueError("image dimensions must be positive")
    return width, height, animated


def _encode_image(image: Image.Image, mime_type: str, attempt: int) -> bytes:
    output = BytesIO()
    if mime_type == "image/jpeg":
        quality = max(45, 90 - attempt * 8)
        image.convert("RGB").save(output, format="JPEG", quality=quality, optimize=True)
    elif mime_type == "image/webp":
        quality = max(45, 90 - attempt * 8)
        image.save(output, format="WEBP", quality=quality, method=4)
    elif mime_type == "image/gif":
        image.save(output, format="GIF", optimize=True)
    else:
        image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _bounded_dimensions(width: int, height: int, maximum: int) -> tuple[int, int]:
    scale = min(1.0, maximum / width, maximum / height)
    return max(1, int(width * scale)), max(1, int(height * scale))


def _classify_png(data: bytes) -> PngKind:
    if len(data) < 33 or int.from_bytes(data[8:12], "big") != 13 or data[12:16] != b"IHDR":
        return "invalid"
    offset = len(PNG_SIGNATURE)
    while offset + 12 <= len(data):
        chunk_length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        if chunk_type == b"acTL":
            return "animated"
        if chunk_type == b"IDAT":
            return "static"
        next_offset = offset + 12 + chunk_length
        if next_offset <= offset or next_offset > len(data):
            return "invalid"
        offset = next_offset
    return "invalid"


def _is_valid_bmp_header(data: bytes) -> bool:
    if len(data) < 30:
        return False
    declared_size = int.from_bytes(data[2:6], "little")
    pixel_offset = int.from_bytes(data[10:14], "little")
    dib_size = int.from_bytes(data[14:18], "little")
    if declared_size and declared_size < 26:
        return False
    if pixel_offset < 14 + dib_size or (declared_size and pixel_offset >= declared_size):
        return False
    if dib_size == 12:
        planes = int.from_bytes(data[22:24], "little")
        bits_per_pixel = int.from_bytes(data[24:26], "little")
    elif 40 <= dib_size <= 124:
        planes = int.from_bytes(data[26:28], "little")
        bits_per_pixel = int.from_bytes(data[28:30], "little")
    else:
        return False
    return planes == 1 and bits_per_pixel in {1, 4, 8, 16, 24, 32}


def _format_size(size: int) -> str:
    return f"{size / (1024 * 1024):.1f}MB"

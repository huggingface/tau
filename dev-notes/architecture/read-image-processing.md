# Bounded read-image processing

## What was added

The `read` tool now validates image bytes with Pillow before attaching them. It
accepts JPEG, static PNG, GIF, WebP, and BMP detected from file content. BMP is
normalized to PNG because the provider APIs do not consistently accept BMP.
Images larger than 2,000 pixels on either side or 5 MB are resized and encoded
again; aspect ratio is preserved and small images are never enlarged.

Processing has explicit ceilings:

- source encoding: 50 MB
- source dimensions: 40 million pixels
- output dimensions: 2,000 by 2,000 maximum
- output encoding: 5 MB
- image header sniff: 64 KB before loading oversized local files
- resize/encode attempts: 12

Pillow was selected because it has maintained cross-platform wheels, supports
the required formats, and exposes decoding validation and decompression-bomb
warnings. Tau converts those warnings and decode/encode failures into text-only
tool results. Limits are checked before loading pixel data where Pillow's
metadata API allows it. For the default local operations, files over 50 MB are
classified from a 64 KB prefix and known image families are rejected before the
full file is read. Large text files retain the existing read behavior.

`ReadOperations` separates path validation and byte reading from the tool's
classification and processing. Optional size and prefix callbacks enable the
early image rejection; implementations that omit them retain the full-read
fallback. The default remains the local filesystem. Tests can supply fake
operations, and a future remote-filesystem integration can do so without moving
local I/O into `tau_agent`.

## Why it exists

The first multimodal implementation correctly carried canonical `ImageContent`
through the agent and provider layers, but rejected every attachment above 5 MB
and trusted shallow signatures. Real screenshots and camera images can exceed a
provider's limit while remaining easy to resize. Malformed or very large decoded
images also need predictable failure behavior.

This follows Pi's separation: coding-specific file/image work happens at the
read-tool boundary, `tau_agent` stores provider-neutral content, and `tau_ai`
serializes it. Tau keeps its existing shared capability helper in
`tau_ai/content.py`; provider adapters still own their wire-specific placement
because tool-result image placement differs across APIs.

## Product and delivery decisions

1. Preserve original supported bytes when they are already safe. This avoids
   quality loss and preserves supported GIF/WebP animation.
2. Convert BMP to PNG, and resize static oversized images. An animated image
   that exceeds limits is omitted rather than silently flattened to one frame.
   Animated PNG and JPEG XL input receive explicit unsupported-format notes
   instead of falling through to UTF-8 decoding.
3. Keep one transformed payload in `ImageContent`. Original bytes and base64 are
   not copied into tool `details` or session JSONL.
4. Share mutable image-capability state between `CodingSession` and the built-in
   `read` tool. Text-only models receive a strong text-only notice before image
   processing, and model changes update that state in place. Provider adapters
   retain the same defensive downgrade for old sessions and other tools.
5. Return transformation notes to the model so it knows when dimensions or
   encoding changed.
6. Keep terminal image rendering out of scope. Textual has no built-in equivalent
   to Pi's terminal-image protocol renderer, so the TUI continues to show the
   textual status. A custom widget or third-party integration can be evaluated
   separately without changing the tool-result contract.
7. Keep live credential checks outside CI. `TODO.md` retains the provider/model
   validation matrix follow-up.

## How to test

Focused behavior:

```bash
uv run pytest tests/test_image_processing.py tests/test_coding_tools.py
```

Full validation:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
cd website && hugo --minify
```

Manual validation should read a small image, a BMP, and a static image above
2,000 pixels with a vision model. Confirm that the model sees each image, BMP is
reported as converted, and the large image reports its resized dimensions.

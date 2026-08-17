import asyncio
import base64
import shlex
from io import BytesIO
from pathlib import Path
from time import monotonic

import pytest
from PIL import Image

from tau_agent import ImageContent
from tau_coding import (
    ImageSupportState,
    ReadOperations,
    create_bash_tool,
    create_bash_tool_definition,
    create_coding_tools,
    create_edit_tool,
    create_edit_tool_definition,
    create_read_tool,
    create_read_tool_definition,
    create_write_tool,
)
from tau_coding.image_processing import DEFAULT_MAX_SOURCE_IMAGE_BYTES


def image_bytes(format_name: str = "PNG", *, size: tuple[int, int] = (8, 6)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "navy").save(output, format=format_name)
    return output.getvalue()


def animated_png_bytes() -> bytes:
    png = image_bytes()
    idat_offset = png.index(b"IDAT") - 4
    animated_chunk = b"\x00\x00\x00\x08acTL\x00\x00\x00\x02\x00\x00\x00\x00" + b"\x00" * 4
    return png[:idat_offset] + animated_chunk + png[idat_offset:]


class FakeCancellationToken:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def is_cancelled(self) -> bool:
        return self.cancelled


@pytest.mark.anyio
async def test_create_coding_tools_returns_initial_tool_set(tmp_path: Path) -> None:
    tools = create_coding_tools(cwd=tmp_path)

    assert [tool.name for tool in tools] == ["read", "write", "edit", "bash"]
    edit_tool = tools[2]
    assert edit_tool.prompt_snippet is not None
    assert "Use edit for precise changes" in edit_tool.prompt_guidelines[0]
    assert "present-participle description" in tools[3].prompt_guidelines[0]


def test_bash_tool_schema_requires_display_description(tmp_path: Path) -> None:
    definition = create_bash_tool_definition(cwd=tmp_path)
    properties = definition.input_schema["properties"]

    assert isinstance(properties, dict)
    assert properties["description"]["type"] == "string"
    assert "present-participle summary" in properties["description"]["description"]
    assert definition.input_schema["required"] == ["command", "description"]


def test_tool_definitions_expose_pi_style_prompt_metadata(tmp_path: Path) -> None:
    definition = create_edit_tool_definition(cwd=tmp_path)

    assert definition.prompt_snippet.startswith("Make precise file edits")
    assert len(definition.prompt_guidelines) == 4


def test_read_tool_schema_defines_line_controls_as_integers(tmp_path: Path) -> None:
    definition = create_read_tool_definition(cwd=tmp_path)
    properties = definition.input_schema["properties"]

    assert isinstance(properties, dict)
    assert properties["offset"]["type"] == "integer"
    assert properties["limit"]["type"] == "integer"


@pytest.mark.anyio
async def test_read_tool_reads_file_with_offset_and_limit(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("one\ntwo\nthree\n")
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"path": "notes.txt", "offset": 2, "limit": 1})

    assert result.text
    assert result.text == "two\n\n[2 more lines in file. Use offset=3 to continue.]"
    assert result.details is not None
    assert result.details["path"] == str(path)
    assert isinstance(result.details["truncation"], dict)


@pytest.mark.anyio
async def test_read_tool_returns_images_as_model_content(tmp_path: Path) -> None:
    image_data = image_bytes()
    path = tmp_path / "diagram.png"
    path.write_bytes(image_data)
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"path": "diagram.png"})

    assert result.text == "Read image file [image/png]"
    image = next(block for block in result.content if isinstance(block, ImageContent))
    assert image.mime_type == "image/png"
    assert base64.b64decode(image.data) == image_data
    assert result.details == {
        "path": str(path),
        "source_mime_type": "image/png",
        "mime_type": "image/png",
        "bytes": len(image_data),
        "processed_bytes": len(image_data),
        "width": 8,
        "height": 6,
    }


@pytest.mark.anyio
async def test_read_tool_detects_images_by_content_not_extension(tmp_path: Path) -> None:
    path = tmp_path / "diagram.txt"
    path.write_bytes(image_bytes())
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"path": "diagram.txt"})

    assert any(isinstance(block, ImageContent) for block in result.content)


@pytest.mark.anyio
async def test_read_tool_resizes_over_dimension_images(tmp_path: Path) -> None:
    path = tmp_path / "large.png"
    path.write_bytes(image_bytes(size=(2_500, 100)))
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"path": "large.png"})

    assert "Image resized from 2500x100 to 2000x80" in result.text
    image = next(block for block in result.content if isinstance(block, ImageContent))
    with Image.open(BytesIO(base64.b64decode(image.data))) as processed:
        assert processed.size == (2_000, 80)


@pytest.mark.anyio
async def test_read_tool_explicitly_omits_images_for_text_only_model(tmp_path: Path) -> None:
    path = tmp_path / "galaxy.png"
    path.write_bytes(image_bytes())
    image_support = ImageSupportState(supported=False)
    tool = create_read_tool(cwd=tmp_path, image_support=image_support)

    omitted = await tool.execute("test-call", {"path": "galaxy.png"})

    assert "current model does not support image input" in omitted.text
    assert "do not infer or describe" in omitted.text
    assert "switch to a vision-capable model" in omitted.text
    assert not any(isinstance(block, ImageContent) for block in omitted.content)

    image_support.supported = True
    attached = await tool.execute("test-call", {"path": "galaxy.png"})

    assert any(isinstance(block, ImageContent) for block in attached.content)


@pytest.mark.anyio
async def test_read_tool_converts_bmp_to_png(tmp_path: Path) -> None:
    path = tmp_path / "legacy.bmp"
    path.write_bytes(image_bytes("BMP"))
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"path": "legacy.bmp"})

    assert "Read image file [image/png]" in result.text
    assert "Image converted from image/bmp to image/png" in result.text
    image = next(block for block in result.content if isinstance(block, ImageContent))
    assert image.mime_type == "image/png"
    assert base64.b64decode(image.data).startswith(b"\x89PNG\r\n\x1a\n")
    assert result.details is not None
    assert result.details["source_mime_type"] == "image/bmp"


@pytest.mark.anyio
async def test_read_tool_reports_decode_failure_without_attachment(tmp_path: Path) -> None:
    malformed = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\x0dIHDR" + b"\x00" * 17 + b"\x00\x00\x00\x00IDAT" + b"\x00" * 4
    )
    (tmp_path / "broken.png").write_bytes(malformed)
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"path": "broken.png"})

    assert "Image omitted: could not decode a valid image" in result.text
    assert not any(isinstance(block, ImageContent) for block in result.content)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "data", "reason"),
    [
        ("animated.png", animated_png_bytes(), "animated PNG images are not supported"),
        ("image.jxl", b"\xff\xd8\xff\xf7not-jpeg", "JPEG XL images are not supported"),
    ],
)
async def test_read_tool_reports_known_unsupported_image_variants(
    tmp_path: Path, filename: str, data: bytes, reason: str
) -> None:
    (tmp_path / filename).write_bytes(data)
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"path": filename})

    assert reason in result.text
    assert not any(isinstance(block, ImageContent) for block in result.content)


@pytest.mark.anyio
async def test_read_tool_rejects_oversized_image_from_prefix_before_full_read(
    tmp_path: Path,
) -> None:
    def unexpected_full_read(path: Path) -> bytes:
        raise AssertionError(f"unexpected full read: {path}")

    source_size = DEFAULT_MAX_SOURCE_IMAGE_BYTES + 1
    operations = ReadOperations(
        validate_path=lambda path: None,
        read_bytes=unexpected_full_read,
        size_bytes=lambda path: source_size,
        read_prefix=lambda path, limit: image_bytes()[:limit],
    )
    tool = create_read_tool(cwd=tmp_path, operations=operations)

    result = await tool.execute("test-call", {"path": "huge.png"})

    assert "exceeding the 50.0MB processing limit" in result.text
    assert not any(isinstance(block, ImageContent) for block in result.content)
    assert result.details is not None
    assert result.details["bytes"] == source_size


@pytest.mark.anyio
async def test_read_tool_still_reads_large_text_files(tmp_path: Path) -> None:
    operations = ReadOperations(
        validate_path=lambda path: None,
        read_bytes=lambda path: b"large text",
        size_bytes=lambda path: DEFAULT_MAX_SOURCE_IMAGE_BYTES + 1,
        read_prefix=lambda path, limit: b"large text"[:limit],
    )
    tool = create_read_tool(cwd=tmp_path, operations=operations)

    result = await tool.execute("test-call", {"path": "large.txt"})

    assert result.text == "large text"


@pytest.mark.anyio
async def test_read_tool_uses_pluggable_read_operations(tmp_path: Path) -> None:
    reads: list[Path] = []
    operations = ReadOperations(
        validate_path=lambda path: None,
        read_bytes=lambda path: reads.append(path) or b"remote text",
    )
    tool = create_read_tool(cwd=tmp_path, operations=operations)

    result = await tool.execute("test-call", {"path": "not-local.txt"})

    assert result.text == "remote text"
    assert reads == [tmp_path / "not-local.txt"]


@pytest.mark.anyio
async def test_read_tool_treats_zero_offset_as_start_of_file(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("one\ntwo\nthree\n")
    tool = create_read_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"path": "notes.txt", "offset": 0, "limit": 1})

    assert result.text
    assert result.text == "one\n\n[3 more lines in file. Use offset=2 to continue.]"


@pytest.mark.anyio
async def test_write_tool_creates_parent_directories(tmp_path: Path) -> None:
    tool = create_write_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"path": "nested/file.txt", "content": "hello"})

    assert result.text
    assert (tmp_path / "nested" / "file.txt").read_text() == "hello"


@pytest.mark.anyio
async def test_edit_tool_applies_multiple_exact_replacements(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_text("alpha\nbeta\ngamma\n")
    tool = create_edit_tool(cwd=tmp_path)

    result = await tool.execute(
        "test-call",
        {
            "path": "file.txt",
            "edits": [
                {"oldText": "alpha", "newText": "one"},
                {"oldText": "gamma", "newText": "three"},
            ],
        },
    )

    assert result.text
    assert path.read_text() == "one\nbeta\nthree\n"


@pytest.mark.anyio
async def test_edit_tool_rolls_back_when_any_edit_fails(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    original = "alpha\nbeta\ngamma\n"
    path.write_text(original)
    tool = create_edit_tool(cwd=tmp_path)

    with pytest.raises(ValueError, match="Could not find edits\\[1\\]"):
        await tool.execute(
            "test-call",
            {
                "path": "file.txt",
                "edits": [
                    {"oldText": "alpha", "newText": "one"},
                    {"oldText": "missing", "newText": "nope"},
                ],
            },
        )

    assert path.read_text() == original


@pytest.mark.anyio
async def test_edit_tool_requires_unique_matches(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_text("repeat\nrepeat\n")
    tool = create_edit_tool(cwd=tmp_path)

    with pytest.raises(ValueError, match="Found 2 occurrences"):
        await tool.execute(
            "test-call",
            {
                "path": "file.txt",
                "edits": [{"oldText": "repeat", "newText": "once"}],
            },
        )


@pytest.mark.anyio
async def test_bash_tool_tolerates_missing_required_display_description(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"command": "printf hello"})

    assert result.text
    assert result.text == "hello"
    assert result.details is not None
    assert result.details["exit_code"] == 0
    assert result.details["timed_out"] is False


@pytest.mark.anyio
async def test_create_coding_tools_applies_shell_command_prefix(
    tmp_path: Path,
) -> None:
    tools = create_coding_tools(
        cwd=tmp_path,
        shell_command_prefix="shopt -s expand_aliases\nalias greet='printf coding-tool-alias'",
    )
    bash_tool = next(tool for tool in tools if tool.name == "bash")

    result = await bash_tool.execute("test-call", {"command": "greet"})

    assert result.text
    assert result.text == "coding-tool-alias"
    assert result.details is not None
    assert result.details["shell_command_prefix_applied"] is True


@pytest.mark.anyio
async def test_bash_tool_applies_opt_in_shell_command_prefix(tmp_path: Path) -> None:
    rc_path = tmp_path / ".zshrc"
    marker = tmp_path / "sourced"
    rc_path.write_text(
        f"alias greet='printf alias-output'\ntouch {shlex.quote(str(marker))}\n",
        encoding="utf-8",
    )
    prefix = f"shopt -s expand_aliases\neval \"$(grep '^alias ' {shlex.quote(str(rc_path))})\""
    tool = create_bash_tool(cwd=tmp_path, shell_command_prefix=prefix)

    result = await tool.execute("test-call", {"command": "greet"})

    assert result.text
    assert result.text == "alias-output"
    assert result.details is not None
    assert result.details["shell_command_prefix_applied"] is True
    assert not marker.exists()


@pytest.mark.anyio
async def test_bash_tool_reports_timeout(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)

    result = await tool.execute("test-call", {"command": "sleep 1", "timeout": 0.01})

    assert result.details is not None
    assert result.details is not None
    assert result.details["timed_out"] is True
    assert "timed out" in result.text


@pytest.mark.anyio
async def test_bash_tool_timeout_kills_shell_children(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)
    marker = tmp_path / "marker"

    start = monotonic()
    result = await tool.execute(
        "test-call", {"command": "(sleep 0.25; touch marker) & wait", "timeout": 0.01}
    )
    duration = monotonic() - start
    await asyncio.sleep(0.35)

    assert result.details is not None
    assert result.details is not None
    assert result.details["timed_out"] is True
    assert duration < 0.5
    assert not marker.exists()


@pytest.mark.anyio
async def test_bash_tool_cancellation_kills_shell_children(tmp_path: Path) -> None:
    tool = create_bash_tool(cwd=tmp_path)
    token = FakeCancellationToken()

    task = asyncio.create_task(
        tool.execute("test-call", {"command": "sleep 1 & wait"}, signal=token)
    )
    await asyncio.sleep(0.05)
    token.cancel()
    start = monotonic()
    result = await task
    duration = monotonic() - start

    assert result.details is not None
    assert result.details is not None
    assert result.details["cancelled"] is True
    assert "cancelled" in result.text
    assert duration < 0.5

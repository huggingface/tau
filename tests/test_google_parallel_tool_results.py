from tau_agent import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tau_ai.google import _build_google_payload


def _payload(model: str, messages: list) -> dict:
    return _build_google_payload(
        model=model,
        system="",
        messages=messages,
        tools=[],
        reasoning_effort=None,
        max_tokens=None,
        supports_images=True,
    )


def _part_kinds(content: dict) -> list:
    return [next(iter(part)) for part in content["parts"]]


def test_parallel_tool_results_share_one_user_content() -> None:
    payload = _payload(
        "gemini-flash-latest",
        [
            UserMessage(content="compare a.py and b.py"),
            AssistantMessage(
                content=[
                    ToolCall(id="c1", name="read", arguments={"path": "a.py"}),
                    ToolCall(id="c2", name="read", arguments={"path": "b.py"}),
                ]
            ),
            ToolResultMessage(tool_call_id="c1", tool_name="read", content="a"),
            ToolResultMessage(tool_call_id="c2", tool_name="read", content="b"),
        ],
    )

    contents = payload["contents"]
    assert [content["role"] for content in contents] == ["user", "model", "user"]
    assert [_part_kinds(content) for content in contents] == [
        ["text"],
        ["functionCall", "functionCall"],
        ["functionResponse", "functionResponse"],
    ]
    responses = contents[2]["parts"]
    assert responses[0]["functionResponse"]["id"] == "c1"
    assert responses[0]["functionResponse"]["response"] == {"output": "a"}
    assert responses[1]["functionResponse"]["id"] == "c2"
    assert responses[1]["functionResponse"]["response"] == {"output": "b"}


def test_parallel_tool_image_followups_stay_after_merged_responses() -> None:
    payload = _payload(
        "gemini-2.5-flash",
        [
            AssistantMessage(
                content=[
                    ToolCall(id="c1", name="read", arguments={"path": "a.png"}),
                    ToolCall(id="c2", name="read", arguments={"path": "b.py"}),
                ]
            ),
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="read",
                content=[
                    TextContent(text="Read image file [image/png]"),
                    ImageContent(data="aW1hZ2U=", mime_type="image/png"),
                ],
            ),
            ToolResultMessage(tool_call_id="c2", tool_name="read", content="b"),
            UserMessage(content="thanks"),
        ],
    )

    contents = payload["contents"]
    assert [content["role"] for content in contents] == ["model", "user", "user", "user"]
    assert _part_kinds(contents[1]) == ["functionResponse", "functionResponse"]
    assert contents[2]["parts"][0] == {"text": "Tool result image:"}
    assert contents[2]["parts"][1] == {"inlineData": {"mimeType": "image/png", "data": "aW1hZ2U="}}
    assert _part_kinds(contents[3]) == ["text"]
    assert contents[3]["parts"][0]["text"] == "thanks"


def test_sequential_tool_rounds_stay_separate_contents() -> None:
    payload = _payload(
        "gemini-flash-latest",
        [
            UserMessage(content="read a then b"),
            AssistantMessage(content=[ToolCall(id="c1", name="read", arguments={"path": "a.py"})]),
            ToolResultMessage(tool_call_id="c1", tool_name="read", content="a"),
            AssistantMessage(content=[ToolCall(id="c2", name="read", arguments={"path": "b.py"})]),
            ToolResultMessage(tool_call_id="c2", tool_name="read", content="b"),
        ],
    )

    contents = payload["contents"]
    assert [_part_kinds(content) for content in contents] == [
        ["text"],
        ["functionCall"],
        ["functionResponse"],
        ["functionCall"],
        ["functionResponse"],
    ]


def test_gemini_3_parallel_image_results_stay_inside_function_responses() -> None:
    payload = _payload(
        "gemini-3-pro",
        [
            AssistantMessage(
                content=[
                    ToolCall(id="c1", name="read", arguments={}),
                    ToolCall(id="c2", name="read", arguments={}),
                ]
            ),
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="read",
                content=[
                    TextContent(text="one"),
                    ImageContent(data="aW1hZ2U=", mime_type="image/png"),
                ],
            ),
            ToolResultMessage(
                tool_call_id="c2",
                tool_name="read",
                content=[
                    TextContent(text="two"),
                    ImageContent(data="aW1hZ2U=", mime_type="image/png"),
                ],
            ),
        ],
    )

    contents = payload["contents"]
    assert [content["role"] for content in contents] == ["model", "user"]
    assert _part_kinds(contents[1]) == ["functionResponse", "functionResponse"]
    first_response = contents[1]["parts"][0]["functionResponse"]
    assert first_response["parts"][0]["inlineData"]["data"] == "aW1hZ2U="
    assert "Tool result image:" not in str(payload)

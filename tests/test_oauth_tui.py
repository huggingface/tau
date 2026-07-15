import asyncio

import pytest
from textual.widgets import Input, Static

from tau_coding.oauth_types import OAuthPrompt
from tau_coding.provider_catalog import builtin_provider_entry
from tau_coding.tui.app import OAuthLoginScreen
from tau_coding.tui.config import TAU_DARK_THEME


@pytest.mark.anyio
async def test_oauth_screen_accepts_blank_provider_prompt() -> None:
    provider = builtin_provider_entry("github-copilot")
    assert provider is not None
    screen = OAuthLoginScreen(provider, theme=TAU_DARK_THEME)
    screen.compose()

    # Exercise the prompt/input handshake inside a minimal Textual app context.
    from textual.app import App

    class TestApp(App[None]):
        def on_mount(self) -> None:
            self.push_screen(screen)

    app = TestApp()
    async with app.run_test() as pilot:
        prompt_task = asyncio.create_task(
            screen._prompt_for_code(OAuthPrompt(message="Enterprise domain", allow_empty=True))
        )
        await pilot.pause()
        screen.query_one("#login-oauth-code", Input).value = ""
        await pilot.press("enter")
        await pilot.pause()

        assert await prompt_task == ""
        assert str(screen.query_one("#login-help", Static).render()) == "Enterprise domain"

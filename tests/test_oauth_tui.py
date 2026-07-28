import asyncio

import pytest
from textual.geometry import Region
from textual.widgets import Input, Static

from tau_coding.oauth_types import OAuthAuthInfo, OAuthPrompt
from tau_coding.provider_catalog import builtin_provider_entry
from tau_coding.tui.app import OAuthLoginScreen, TauTuiApp
from tau_coding.tui.config import TAU_DARK_THEME


@pytest.mark.anyio
async def test_oauth_screen_shows_full_authorization_url() -> None:
    """The whole URL must be visible: users copy it out of the TUI by hand."""
    from textual.app import App

    from tau_coding.tui.app import _textual_theme_for_tau_theme

    provider = builtin_provider_entry("anthropic")
    assert provider is not None
    url = "https://claude.ai/oauth/authorize?" + "&".join(f"p{index}=value" for index in range(40))
    assert len(url) > 400

    async def fake_login(callbacks):
        callbacks.on_auth(OAuthAuthInfo(url=url))
        await asyncio.Event().wait()

    screen = OAuthLoginScreen(provider, theme=TAU_DARK_THEME, login=fake_login)

    class TestApp(App[None]):
        CSS = TauTuiApp.CSS

        def __init__(self) -> None:
            super().__init__()
            self.register_theme(_textual_theme_for_tau_theme(TAU_DARK_THEME.name))
            self.theme = TAU_DARK_THEME.name

        def on_mount(self) -> None:
            self.push_screen(screen)

    copied: list[str] = []
    app = TestApp()
    app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        widget = screen.query_one("#login-oauth-url", Static)
        lines = widget.render_lines(Region(0, 0, widget.size.width, widget.size.height))
        rendered = "".join("".join(segment.text for segment in line) for line in lines)
        links = {
            segment.style.link
            for line in lines
            for segment in line
            if segment.style is not None and segment.style.link
        }

    assert url in rendered.replace(" ", "")
    # Every wrapped line links to the intact URL, and it is on the clipboard, so
    # the user never has to reassemble it from the wrapped display by hand.
    assert links == {url}
    assert copied == [url]


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

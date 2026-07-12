from unittest.mock import patch

from tau_coding.notification import send_notification


def test_send_notification_writes_osc9() -> None:
    with patch("tau_coding.notification.os.write") as mock_write:
        send_notification("Waiting for your input")
        mock_write.assert_called_once_with(2, b"\x1b]9;Waiting for your input\x07\r\x1b[K")


def test_send_notification_escape_stx() -> None:
    """Verify control chars in message are stripped."""
    with patch("tau_coding.notification.os.write") as mock_write:
        send_notification("Bell: \x07, Esc: \x1b")
        mock_write.assert_called_once_with(2, b"\x1b]9;Bell: , Esc: \x07\r\x1b[K")


def test_send_notification_handles_oserror() -> None:
    with patch("tau_coding.notification.os.write", side_effect=OSError()):
        send_notification("Test")  # Should not raise

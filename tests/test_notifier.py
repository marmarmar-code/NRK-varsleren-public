import httpx
import pytest

from nrk_journal_monitor.notifier import NotificationError, SlackNotifier


def test_slack_acceptance() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="ok")

    with SlackNotifier(
        "https://hooks.slack.com/services/AAA/BBB/CCC",
        transport=httpx.MockTransport(handler),
    ) as notifier:
        notifier.send_text("test")
    assert len(requests) == 1


def test_invalid_webhook_error_does_not_echo_secret() -> None:
    secret = "not-a-valid-webhook-secret"
    with SlackNotifier(secret, transport=httpx.MockTransport(lambda _r: httpx.Response(200))) as notifier:
        with pytest.raises(NotificationError) as captured:
            notifier.send_text("test")
    assert secret not in str(captured.value)

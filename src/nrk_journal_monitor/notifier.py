from __future__ import annotations

import random
import re
import time
from collections.abc import Callable

import httpx

from .models import Event
from .source import RETRYABLE_STATUS_CODES


class NotificationError(RuntimeError):
    """Slack error that never includes the webhook URL."""


_SLACK_WEBHOOK_PATTERN = re.compile(
    r"(?P<scheme>https?://)?(?P<host>hooks\.slack(?:-gov)?\.com)"
    r"(?P<path>/services/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+)",
    re.I,
)


def normalize_slack_webhook_url(value: str) -> str:
    normalized = value.strip().strip("'\"")
    name, separator, assigned = normalized.partition("=")
    if separator and name.strip().upper() in {"SLACKWEBHOOK", "SLACK_WEBHOOK_URL"}:
        normalized = assigned.strip().strip("'\"")
    match = _SLACK_WEBHOOK_PATTERN.search(normalized)
    if match is None:
        raise NotificationError("SLACK_WEBHOOK_URL is missing or invalid")
    url = f"https://{match.group('host').lower()}{match.group('path')}"
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        raise NotificationError("SLACK_WEBHOOK_URL is missing or invalid") from None
    if parsed.scheme != "https" or parsed.host not in {
        "hooks.slack.com",
        "hooks.slack-gov.com",
    }:
        raise NotificationError("SLACK_WEBHOOK_URL is missing or invalid")
    return str(parsed)


class SlackNotifier:
    def __init__(
        self,
        webhook_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        max_attempts: int = 3,
    ) -> None:
        self._candidate = webhook_url
        self._url: str | None = None
        self._sleep = sleep
        self._jitter = jitter
        self._max_attempts = max_attempts
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SlackNotifier:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def send(self, event: Event) -> None:
        self.send_text(event.slack_message())

    def send_text(self, text: str) -> None:
        if self._url is None:
            self._url = normalize_slack_webhook_url(self._candidate)
        for attempt in range(self._max_attempts):
            try:
                response = self._client.post(self._url, json={"text": text})
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt + 1 == self._max_attempts:
                    raise NotificationError(
                        "Slack request failed after temporary transport errors"
                    ) from None
                self._sleep(min(4.0, 0.5 * (2**attempt)) + self._jitter() * 0.25)
                continue
            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt + 1 == self._max_attempts:
                    raise NotificationError(
                        f"Slack remained temporarily unavailable (HTTP {response.status_code})"
                    )
                self._sleep(min(4.0, 0.5 * (2**attempt)) + self._jitter() * 0.25)
                continue
            if response.status_code != 200:
                raise NotificationError(
                    f"Slack rejected the notification (HTTP {response.status_code})"
                )
            return

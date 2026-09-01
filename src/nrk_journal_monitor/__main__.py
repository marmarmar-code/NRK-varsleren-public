from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .models import Event, JournalObservation
from .notifier import NotificationError, SlackNotifier
from .runner import RunFailed, bootstrap, check
from .source import NrkJournalSource, SourceError


DEFAULT_STATE = Path("state/monitor_state.json")
TEST_OBSERVATION = JournalObservation(
    date_from="2099-01-01",
    date_to="2099-01-07",
    title="FIKTIV offentlig journal 01.01.2099–07.01.2099",
    source_url="https://info.nrk.no/fiktiv-journal-test.pdf",
)
_ALLOWED_FAILURE_ALERT_REPOSITORIES = {
    "marmarmar-code/NRK-varsleren",
    "marmarmar-code/NRK-varsleren-public",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NRK public-journal monitor")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bootstrap")
    commands.add_parser("run")
    commands.add_parser("test-source")
    commands.add_parser("test-slack")
    commands.add_parser("test-event-slack")
    commands.add_parser("notify-failure")
    check_parser = commands.add_parser("check")
    check_parser.add_argument("--dry-run", action="store_true", required=True)
    return parser


def _test_event_slack_message() -> str:
    return "\n".join(
        [
            "*:test_tube: TEST – FIKTIV NRK-JOURNAL*",
            "Kun visuell test av den faktiske eventrenderingen i Slack.",
            "",
            Event.from_observation(TEST_OBSERVATION).slack_message(),
            "",
            "_TEST: Ingen NRK-kall. Ingen state lest eller endret._",
        ]
    )


def _scheduled_failure_slack_message() -> str:
    server_url = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if (
        server_url != "https://github.com"
        or repository not in _ALLOWED_FAILURE_ALERT_REPOSITORIES
        or not run_id.isdigit()
    ):
        raise NotificationError("GitHub run metadata is invalid for failure alert")
    run_url = f"{server_url}/{repository}/actions/runs/{run_id}"
    return "\n".join(
        [
            ":rotating_light: *NRK-journalmonitoren feilet*",
            "Den automatiske kontrollen fullførte ikke med grønn status.",
            f"<{run_url}|Åpne feilet GitHub-kjøring>",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if args.command in {"test-slack", "test-event-slack", "notify-failure"}:
        try:
            if args.command == "test-event-slack":
                with SlackNotifier(webhook, max_attempts=1) as notifier:
                    notifier.send_text(_test_event_slack_message())
            elif args.command == "notify-failure":
                with SlackNotifier(webhook) as notifier:
                    notifier.send_text(_scheduled_failure_slack_message())
            else:
                with SlackNotifier(webhook) as notifier:
                    notifier.send_text(
                        "NRK-journalmonitor: Slack-test godkjent. Dette er ikke en "
                        "journalhendelse, og state ble ikke endret."
                    )
        except NotificationError as exc:
            print(f"Final status: failed\nError: {exc}", file=sys.stderr)
            return 1
        print("Slack notification accepted. NRK requests: 0. State: unchanged.")
        return 0

    if args.command == "test-source":
        try:
            with NrkJournalSource() as source:
                observations = source.fetch_journals()
        except SourceError as exc:
            print(
                f"Final status: failed\nError: Source check failed: {type(exc).__name__}",
                file=sys.stderr,
            )
            return 1
        print(
            f"Observed periods: {len(observations)}\n"
            "Final status: trusted\n"
            "Slack notifications: 0\n"
            "State: unchanged"
        )
        return 0

    try:
        with NrkJournalSource() as source:
            if args.command == "bootstrap":
                summary = bootstrap(source, args.state)
            elif args.command == "check":
                summary = check(source, args.state, dry_run=True)
            else:
                with SlackNotifier(webhook) as notifier:
                    summary = check(
                        source, args.state, dry_run=False, notifier=notifier
                    )
    except (RunFailed, NotificationError) as exc:
        if isinstance(exc, RunFailed):
            print(exc.summary.render(), file=sys.stderr)
        else:
            print(f"Final status: failed\nError: {exc}", file=sys.stderr)
        return 1
    print(summary.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

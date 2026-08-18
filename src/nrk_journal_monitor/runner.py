from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .detector import detect_new_journals
from .models import Event, JournalObservation
from .state import apply_accepted_events, create_baseline, load_state, write_state_atomic


class JournalSource(Protocol):
    def fetch_journals(self) -> list[JournalObservation]: ...


class EventNotifier(Protocol):
    def send(self, event: Event) -> None: ...


@dataclass(slots=True)
class RunSummary:
    observed_periods: int = 0
    new_events: int = 0
    slack_accepted: int = 0
    state_changed: bool = False
    trusted: bool = False
    error: str | None = None

    def render(self) -> str:
        lines = [
            f"Observed periods: {self.observed_periods}",
            f"New events: {self.new_events}",
            f"Slack notifications accepted: {self.slack_accepted}",
            f"State: {'changed' if self.state_changed else 'unchanged'}",
            f"Final status: {'trusted' if self.trusted else 'failed'}",
        ]
        if self.error:
            lines.append(f"Error: {self.error}")
        return "\n".join(lines)


class RunFailed(RuntimeError):
    def __init__(self, summary: RunSummary):
        super().__init__(summary.error or "Monitor run failed")
        self.summary = summary


def _fetch(source: JournalSource, summary: RunSummary) -> list[JournalObservation]:
    try:
        observations = source.fetch_journals()
    except Exception as exc:
        summary.error = f"Source check failed: {type(exc).__name__}"
        raise RunFailed(summary) from None
    if not observations:
        summary.error = "Source returned no trusted journal periods"
        raise RunFailed(summary)
    summary.observed_periods = len(observations)
    return observations


def bootstrap(source: JournalSource, state_path: Path) -> RunSummary:
    summary = RunSummary()
    observations = _fetch(source, summary)
    baseline = create_baseline(observations)
    try:
        write_state_atomic(state_path, baseline)
    except OSError:
        summary.error = "Could not commit baseline state"
        raise RunFailed(summary) from None
    summary.state_changed = True
    summary.trusted = True
    return summary


def check(
    source: JournalSource,
    state_path: Path,
    *,
    dry_run: bool,
    notifier: EventNotifier | None = None,
    output: Callable[[str], None] = print,
) -> RunSummary:
    summary = RunSummary()
    try:
        state = load_state(state_path)
    except Exception as exc:
        summary.error = f"State is invalid: {type(exc).__name__}"
        raise RunFailed(summary) from None
    if not state["baseline"]["valid"]:
        summary.error = "A valid baseline is required; run bootstrap first"
        raise RunFailed(summary)

    observations = _fetch(source, summary)
    events = detect_new_journals(observations, set(state["periods"]))
    summary.new_events = len(events)
    if dry_run:
        for event in events:
            output(event.slack_message())
            output("")
        summary.trusted = True
        return summary

    if events and notifier is None:
        summary.error = "A Slack notifier is required for new events"
        raise RunFailed(summary)
    for event in events:
        try:
            notifier.send(event)  # type: ignore[union-attr]
        except Exception as exc:
            summary.error = f"Notification failed: {type(exc).__name__}"
            raise RunFailed(summary) from None
        summary.slack_accepted += 1
    if events:
        updated = apply_accepted_events(state, events)
        try:
            write_state_atomic(state_path, updated)
        except OSError:
            summary.error = (
                "Slack accepted notifications, but state commit failed; retries use "
                "the same event IDs"
            )
            raise RunFailed(summary) from None
        summary.state_changed = True
    summary.trusted = True
    return summary

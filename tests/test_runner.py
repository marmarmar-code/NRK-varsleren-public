import json

import pytest

from nrk_journal_monitor.models import JournalObservation
from nrk_journal_monitor.runner import RunFailed, bootstrap, check
from nrk_journal_monitor.state import create_baseline, write_state_atomic


OLD = JournalObservation("2026-08-01", "2026-08-07", "Journal 1", "https://nrk.no/a.pdf")
NEW = JournalObservation("2026-08-08", "2026-08-14", "Journal 2", "https://nrk.no/b.pdf")


class Source:
    def __init__(self, items):
        self.items = items

    def fetch_journals(self):
        return self.items


class Notifier:
    def __init__(self, fail=False):
        self.fail = fail
        self.events = []

    def send(self, event):
        if self.fail:
            raise RuntimeError("no")
        self.events.append(event)


def test_bootstrap_saves_without_notification(tmp_path) -> None:
    path = tmp_path / "state.json"
    summary = bootstrap(Source([OLD]), path)
    assert summary.trusted and summary.state_changed
    assert json.loads(path.read_text())["baseline"]["valid"] is True


def test_dry_run_does_not_change_state(tmp_path) -> None:
    path = tmp_path / "state.json"
    write_state_atomic(path, create_baseline([OLD]))
    before = path.read_bytes()
    output = []
    summary = check(Source([OLD, NEW]), path, dry_run=True, output=output.append)
    assert summary.new_events == 1
    assert path.read_bytes() == before
    assert any("Ny offentlig journal" in line for line in output)


def test_notification_failure_preserves_state(tmp_path) -> None:
    path = tmp_path / "state.json"
    write_state_atomic(path, create_baseline([OLD]))
    before = path.read_bytes()
    with pytest.raises(RunFailed, match="Notification"):
        check(Source([OLD, NEW]), path, dry_run=False, notifier=Notifier(fail=True))
    assert path.read_bytes() == before


def test_successful_notification_updates_state(tmp_path) -> None:
    path = tmp_path / "state.json"
    write_state_atomic(path, create_baseline([OLD]))
    notifier = Notifier()
    summary = check(Source([OLD, NEW]), path, dry_run=False, notifier=notifier)
    assert summary.slack_accepted == 1 and summary.state_changed
    assert NEW.identity in json.loads(path.read_text())["periods"]

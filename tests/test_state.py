import json

import pytest

from nrk_journal_monitor.models import Event, JournalObservation
from nrk_journal_monitor.state import (
    StateError,
    apply_accepted_events,
    create_baseline,
    load_state,
    validate_state,
    write_state_atomic,
)


def item() -> JournalObservation:
    return JournalObservation("2026-08-01", "2026-08-07", "Journal", "https://nrk.no/a.pdf")


def test_baseline_round_trip(tmp_path) -> None:
    state = create_baseline([item()], timestamp="2026-08-14T10:00:00+00:00")
    path = tmp_path / "state.json"
    write_state_atomic(path, state)
    assert load_state(path) == state
    assert state["periods"][item().identity]["notification_status"] == "baseline"


def test_invalid_schema_is_rejected() -> None:
    with pytest.raises(StateError, match="schema"):
        validate_state({"schema_version": 99})


def test_accepted_event_is_added_without_removing_baseline() -> None:
    state = create_baseline([item()], timestamp="2026-08-14T10:00:00+00:00")
    newer = JournalObservation("2026-08-08", "2026-08-14", "Journal 2", "https://nrk.no/b.pdf")
    updated = apply_accepted_events(
        state, [Event.from_observation(newer)], timestamp="2026-08-15T10:00:00+00:00"
    )
    assert set(updated["periods"]) == {item().identity, newer.identity}
    assert updated["periods"][newer.identity]["notification_status"] == "accepted"


def test_corrupt_json_is_rejected(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(StateError):
        load_state(path)

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Event, JournalObservation
from .source import SOURCE_URL


SCHEMA_VERSION = 1


class StateError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "baseline": {"valid": False, "created_at": None, "source": SOURCE_URL},
        "periods": {},
    }


def validate_state(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StateError("State must be a JSON object")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise StateError("Unsupported state schema version")
    baseline = value.get("baseline")
    if not isinstance(baseline, dict) or not isinstance(baseline.get("valid"), bool):
        raise StateError("State baseline marker is invalid")
    if baseline.get("valid") and not isinstance(baseline.get("created_at"), str):
        raise StateError("Valid baseline must have created_at")
    if baseline.get("source") != SOURCE_URL:
        raise StateError("State source does not match this monitor")
    periods = value.get("periods")
    if not isinstance(periods, dict):
        raise StateError("State periods must be an object")
    for identity, period in periods.items():
        if not isinstance(identity, str) or not isinstance(period, dict):
            raise StateError("State period entry is invalid")
        required_strings = (
            "date_from",
            "date_to",
            "event_id",
            "first_seen_at",
            "notification_status",
            "source_url",
            "title",
        )
        if any(not isinstance(period.get(key), str) for key in required_strings):
            raise StateError("State period metadata is invalid")
        try:
            observation = JournalObservation(
                period["date_from"],
                period["date_to"],
                period["title"],
                period["source_url"],
            )
        except (TypeError, ValueError) as exc:
            raise StateError("State period dates are invalid") from exc
        if observation.identity != identity:
            raise StateError("State period identity is invalid")
        if period["notification_status"] not in {"baseline", "accepted"}:
            raise StateError("State notification status is invalid")
    return value


def load_state(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file_handle:
            return validate_state(json.load(file_handle))
    except StateError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"Could not read valid state: {path}") from exc


def write_state_atomic(path: Path, state: dict[str, Any]) -> None:
    validate_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_handle:
            json.dump(state, file_handle, ensure_ascii=False, indent=2, sort_keys=True)
            file_handle.write("\n")
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary_name, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _period_record(
    observation: JournalObservation,
    *,
    status: str,
    timestamp: str,
) -> dict[str, str]:
    event = Event.from_observation(observation)
    return {
        "date_from": observation.date_from,
        "date_to": observation.date_to,
        "event_id": event.event_id,
        "first_seen_at": timestamp,
        "notification_status": status,
        "source_url": observation.source_url,
        "title": observation.title,
    }


def create_baseline(
    observations: list[JournalObservation], *, timestamp: str | None = None
) -> dict[str, Any]:
    if not observations:
        raise StateError("Cannot create an empty baseline")
    seen_at = timestamp or utc_now()
    state = empty_state()
    state["baseline"] = {"valid": True, "created_at": seen_at, "source": SOURCE_URL}
    for observation in observations:
        state["periods"][observation.identity] = _period_record(
            observation, status="baseline", timestamp=seen_at
        )
    return validate_state(state)


def apply_accepted_events(
    state: dict[str, Any], events: list[Event], *, timestamp: str | None = None
) -> dict[str, Any]:
    updated = deepcopy(state)
    seen_at = timestamp or utc_now()
    for event in events:
        observation = JournalObservation(
            event.date_from, event.date_to, event.title, event.source_url
        )
        updated["periods"][event.identity] = _period_record(
            observation, status="accepted", timestamp=seen_at
        )
    return validate_state(updated)

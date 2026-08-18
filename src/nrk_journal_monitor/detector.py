from __future__ import annotations

from .models import Event, JournalObservation


def detect_new_journals(
    observations: list[JournalObservation], known_periods: set[str]
) -> list[Event]:
    events = [
        Event.from_observation(observation)
        for observation in observations
        if observation.identity not in known_periods
    ]
    return sorted(events, key=lambda event: (event.date_from, event.date_to))

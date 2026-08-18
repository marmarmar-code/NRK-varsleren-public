from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256


EVENT_KIND = "JOURNAL_FIRST_OBSERVED"


def _escape_slack_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def period_key(date_from: str, date_to: str) -> str:
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if end < start:
        raise ValueError("Journalperioden slutter før den starter")
    return f"{start.isoformat()}_{end.isoformat()}"


@dataclass(frozen=True, slots=True)
class JournalObservation:
    date_from: str
    date_to: str
    title: str
    source_url: str

    def __post_init__(self) -> None:
        period_key(self.date_from, self.date_to)
        if not self.title.strip():
            raise ValueError("Journal title is required")

    @property
    def identity(self) -> str:
        return period_key(self.date_from, self.date_to)


@dataclass(frozen=True, slots=True)
class Event:
    date_from: str
    date_to: str
    title: str
    source_url: str
    event_id: str
    kind: str = EVENT_KIND

    @classmethod
    def from_observation(cls, observation: JournalObservation) -> Event:
        identity = (
            f"nrk-journal-event:v1:{EVENT_KIND}:"
            f"{observation.date_from}:{observation.date_to}"
        )
        return cls(
            date_from=observation.date_from,
            date_to=observation.date_to,
            title=observation.title,
            source_url=observation.source_url,
            event_id=sha256(identity.encode("utf-8")).hexdigest(),
        )

    @property
    def identity(self) -> str:
        return period_key(self.date_from, self.date_to)

    def slack_message(self) -> str:
        title = _escape_slack_text(self.title)
        return "\n".join(
            [
                "*Ny offentlig journal fra NRK*",
                title,
                f"Periode: {self.date_from}–{self.date_to}",
                f"<{self.source_url}|Åpne journalen hos NRK>",
                "",
                f"Hendelse: {self.kind}",
                f"Hendelses-ID: {self.event_id}",
            ]
        )

from nrk_journal_monitor.models import Event, JournalObservation


def observation(url: str = "https://info.nrk.no/journal.pdf") -> JournalObservation:
    return JournalObservation("2026-08-01", "2026-08-07", "Offentlig journal", url)


def test_event_id_is_stable_when_url_changes() -> None:
    first = Event.from_observation(observation("https://info.nrk.no/a.pdf"))
    second = Event.from_observation(observation("https://www.nrk.no/b.pdf"))
    assert first.event_id == second.event_id


def test_event_id_changes_for_a_different_period() -> None:
    first = Event.from_observation(observation())
    second = Event.from_observation(
        JournalObservation("2026-08-08", "2026-08-14", "Offentlig journal", "https://nrk.no/b.pdf")
    )
    assert first.event_id != second.event_id


def test_slack_message_escapes_source_text() -> None:
    event = Event.from_observation(
        JournalObservation("2026-08-01", "2026-08-07", "Journal <test> & kontroll", "https://nrk.no/a.pdf")
    )
    message = event.slack_message()
    assert "Journal &lt;test&gt; &amp; kontroll" in message
    assert event.event_id in message

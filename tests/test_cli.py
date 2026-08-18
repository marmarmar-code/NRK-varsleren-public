from nrk_journal_monitor import __main__ as cli
from nrk_journal_monitor.models import JournalObservation
from nrk_journal_monitor.source import SourceError


OBSERVATION = JournalObservation(
    "2026-08-03",
    "2026-08-09",
    "Offentlig journal 03.08.2026-09.08.2026",
    "https://info.nrk.no/journal.pdf",
)


class Source:
    def __init__(self, *, error: bool = False) -> None:
        self.error = error

    def __enter__(self) -> "Source":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def fetch_journals(self) -> list[JournalObservation]:
        if self.error:
            raise SourceError("blocked")
        return [OBSERVATION]


def test_source_command_is_read_only(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "NrkJournalSource", Source)

    assert cli.main(["test-source"]) == 0
    output = capsys.readouterr().out
    assert "Observed periods: 1" in output
    assert "Slack notifications: 0" in output
    assert "State: unchanged" in output


def test_source_command_fails_closed(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "NrkJournalSource", lambda: Source(error=True))

    assert cli.main(["test-source"]) == 1
    assert "Source check failed: SourceError" in capsys.readouterr().err

# NRK-varsleren

En liten, separat GitHub Actions-monitor som varsler i Slack når en offentlig
journalperiode først observeres på NRKs innsynsside.

`JOURNAL_FIRST_OBSERVED` betyr bare at perioden nå er observert. Det er ikke en
påstand om når journalen ble opprettet eller publisert.

## Sikker arbeidsflyt

1. Kjør testene.
2. Kjør `bootstrap` for å lagre eksisterende perioder uten varsler.
3. Kjør `dry-run`.
4. Kjør `test-slack` og bekreft meldingen visuelt.
5. Kjør én manuell `run`.
6. Sett repository-variabelen `MONITOR_ENABLED=true` for å aktivere timeplanen.

Planlagt drift er deaktivert som standard. Ingen kommando leser en `.env`-fil.
Slack-webhooken skal ligge i repository-secret `SLACKWEBHOOK`.

## Lokalt

```bash
python -m pip install '.[test]'
python -m pytest
python -m nrk_journal_monitor test-source
python -m nrk_journal_monitor --state state/monitor_state.json bootstrap
python -m nrk_journal_monitor --state state/monitor_state.json check --dry-run
```

Kommandoene `test-source`, `bootstrap`, `check --dry-run` og `run` bruker den
virkelige NRK-kilden. `test-source` leser bare kilden og endrer aldri state eller
sender Slack. Ikke kjør live-kall uten godkjenning.

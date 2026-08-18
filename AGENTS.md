# AGENTS.md

## Formål

Dette repoet er en liten, separat varsler for NRKs offentlige journaler. Den
eneste støttede hendelsen er `JOURNAL_FIRST_OBSERVED`.

## Kildegrense

- Kilden er `https://info.nrk.no/innsyn/`.
- Tillat bare HTTPS-lenker på `nrk.no` eller underdomener.
- En kildefeil eller tvetydig journalkandidat er aldri «ingen nye journaler».
- Behandle perioden `date_from + date_to` som journalens stabile identitet.
- Ikke last ned eller analyser PDF-ene i monitoren.
- Live-kall brukes bare når brukeren uttrykkelig har godkjent dem.

## Sikkerhet og tilstand

- Les Slack-webhook bare fra repository-secret `SLACKWEBHOOK`, mappet til
  `SLACK_WEBHOOK_URL`; aldri logg eller lagre verdien.
- Ikke opprett eller les `.env`-filer.
- `test-event-slack` skal gjøre høyst ett Slack-forsøk og aldri lese kilde eller
  state.
- Endre ikke state ved kildefeil, Slack-avvisning eller annen upålitelig kjøring.
- Skriv JSON-state atomisk og behold alle kjente perioder.
- Tester skal være offline og bruke mock-transport.
- Planlagt drift er av til repository-variabelen `MONITOR_ENABLED` eksplisitt er
  satt til `true`.

## Arbeidsmåte

- Hold løsningen liten: Python 3.12, `httpx`, JSON-state og GitHub Actions.
- Kjør målrettede tester under arbeid og hele testpakken før levering.
- Ikke send Slack-meldinger, bootstrap state eller aktiver schedule uten
  uttrykkelig godkjenning på handlingstidspunktet.

# OODA

Ett lugnt ärendehanteringssystem byggt kring OODA-loopen: **Observe → Orient → Decide → Act → Observe …**

- **Ärenden** går runt i loopen. Varje varv räknas, och tiden i varje fas mäts.
- **Inlägg** (observation, orientering, beslut, action) bildar ärendets logg, varv för varv.
- **Inkorg** för lösa inlägg som ännu inte hör till något ärende.
- **Underärenden**, **sammanslagning** och **beroenden** ("väntar på").
- **Taxonomi**: egna fält med fasta val. Filtrera och gruppera tavlan på dem.
- **Tabell** och **tavla** (dra kort mellan kolumner), sök och filter.
- **Marginalkommentarer** i Google Docs-stil på markerad text, med @omnämnanden.
- **Tempo**: typisk looptid, tid per fas, loopar per vecka, vad som står still.
- **E-postnotiser**: tilldelning, omnämnande, när ett beroende blir klart, när en deadline närmar sig.
- **Konto** med ägare och medlemmar, inbjudningslänkar och en publik sida som bara går att läsa.
- Alla texter är markerade för översättning (se `locale/README.md`). Engelska först.

Tekniken är Django och SQLite, med servergenererade sidor och lite vanilla-JS.

## Köra lokalt

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export DEBUG=1
.venv/bin/python manage.py migrate
.venv/bin/python manage.py demo        # valfritt: exempeldata, demo@example.com / demo-password
.venv/bin/python manage.py runserver
```

Öppna http://localhost:8000. Utan demodata kommer du till en sida där du skapar första kontot. E-post skrivs ut i terminalen.

Tester: `DEBUG=1 .venv/bin/python manage.py test`

## Driftsätta på ooda.obrydd.com

Det som behövs är en liten Linux-server med Docker, t.ex. Hetzner CX22.

1. Peka DNS: skapa en `A`-post för `ooda.obrydd.com` som pekar på serverns IP-adress.
2. Installera Docker på servern och klona repot.
3. Skapa konfigurationen:
   ```sh
   cp .env.example .env
   ```
   Fyll i `SECRET_KEY`, `SETUP_KEY` och e-postuppgifterna.
4. Starta:
   ```sh
   docker compose up -d --build
   ```
   Caddy hämtar ett HTTPS-certifikat automatiskt.
5. Öppna `https://ooda.obrydd.com/start/?key=<SETUP_KEY>` och skapa dig själv som ägare.
6. Bjud in andra under **Settings → People → New invitation link**.

**Uppdatera:** `git pull && docker compose up -d --build`. Databasen migreras automatiskt.

**Backup:** all data ligger i SQLite-filen i volymen `data`. Till exempel varje natt:

```sh
docker compose exec web python -c "import sqlite3; s=sqlite3.connect('/data/ooda.sqlite3'); d=sqlite3.connect('/data/backup.sqlite3'); s.backup(d)"
docker compose cp web:/data/backup.sqlite3 ./backups/ooda-$(date +%F).sqlite3
```

### E-post

Allt skickas via SMTP, så valfri leverantör fungerar:

| Leverantör | Värd | Notering |
|---|---|---|
| Postmark | `smtp.postmarkapp.com` | Mycket bra leverans. Användarnamn och lösenord är båda din *Server API token*. |
| Brevo | `smtp-relay.brevo.com` | EU-baserad, generös gratisnivå. |
| Scaleway TEM | `smtp.tem.scw.cloud` | EU-baserad, billig. |

Verifiera avsändardomänen (`obrydd.com`) hos leverantören (SPF/DKIM), annars hamnar mejlen i skräpposten.

## Struktur

```
accounts/   användare, konto (workspace), medlemskap, inbjudningar, inställningar
board/      ärenden, faser, inlägg, kommentarer, taxonomi, tempo, notiser, publik vy
templates/  HTML- och e-postmallar
static/     ooda.css, ooda.js
locale/     översättningar
```

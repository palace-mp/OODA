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

Det som behövs är en Ubuntu 24.04-server, t.ex. Hetzner CX22. Logga in med SSH och kör:

```sh
curl -fsSL https://raw.githubusercontent.com/palace-mp/ooda/main/deploy/setup.sh -o setup.sh
sudo bash setup.sh
```

Skriptet gör följande:
- installerar Docker
- sätter upp en brandvägg och automatiska säkerhetsuppdateringar
- hämtar koden till `/opt/ooda`
- skapar `.env` med slumpade nycklar och frågar efter e-postuppgifter (de kan lämnas tomma)
- startar tjänsten
- lägger upp nattlig backup till `/var/backups/ooda`

Till sist skrivs serverns IP-adress och länken för att skapa första ägaren ut.

Peka sedan DNS för `ooda.obrydd.com` mot IP-adressen. Caddy hämtar HTTPS-certifikatet automatiskt.

**Uppdatera:** `sudo bash /opt/ooda/deploy/update.sh`

**Ändra inställningar**, t.ex. e-post: redigera `/opt/ooda/.env` och kör `docker compose up -d` i `/opt/ooda`.

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

# weddies-data

Wedstrijddata voor de Weddies-app. Een GitHub Action haalt het hele seizoen van de gevolgde competities op bij
[API-Football](https://www.api-football.com) (Pro-plan) en zet via GitHub Pages alleen online wat de app nodig heeft,
in een eigen compact formaat:

- `index.json`: wanneer wat is bijgewerkt (en de API-tellerstand volgens API-Football)
- `wedstrijden/<yyyy-mm-dd>.json`: de wedstrijden van die dag, van 7 dagen terug tot 60 dagen vooruit
- `competities/<id>.json`: het hele seizoen van één competitie (zelfde formaat; `datum` is dan het seizoensjaar)
- `seizoen.json`: clubs per competitie in het huidige seizoen (met groepen, bv. Derde Divisie zaterdag)
- `standen/<id>.json`: de stand van één competitie, per poule (zie hieronder)
- `privacy.html` en `support.html`: het privacybeleid en de supportpagina van de app (bron: `pagina/`)

Het hele seizoen wordt elke 3 uur ververst; live standen van vandaag elke 5 minuten (alleen als er iets bezig is).
De stand van een competitie wordt bij die seizoensruns ververst tot ±6 uur nadat er een wedstrijd klaar is.

De API-sleutel staat als GitHub Secret `API_FOOTBALL_KEY` in deze repository en zit niet in de app.
Schema en rekensom: zie `.github/workflows/wedstrijden.yml`.

## Formaat van `wedstrijden/<datum>.json` (formaat 1)

```json
{
  "formaat": 1,
  "datum": "2026-10-12",
  "competities": {"88": {"naam": "Eredivisie", "land": "Netherlands", "seizoen": 2026}},
  "wedstrijden": [
    {"id": 1552188, "competitie": 88, "ronde": "Regular Season - 9", "aftrap": "2026-10-12T20:00:00+02:00",
     "status": "2H", "minuut": 67, "thuis": {"id": 194, "naam": "Ajax"}, "uit": {"id": 197, "naam": "PSV"},
     "stand": [2, 1], "stadion": "Johan Cruijff ArenA", "plaats": "Amsterdam"}
  ]
}
```

- `status` is de korte status van API-Football (`NS`, `1H`, `HT`, `2H`, `FT`, `PST`, ...).
- `minuut`, `stand`, `ronde`, `stadion` en `plaats` ontbreken als ze leeg zijn.
- Logo's staan er niet in: `https://media.api-sports.io/football/teams/<id>.png` en `.../leagues/<id>.png`.
- `formaat` gaat omhoog bij een verandering die oudere app-versies niet meer kunnen lezen.

## Formaat van `standen/<id>.json` (formaat 1)

```json
{
  "formaat": 1,
  "competitie": 42,
  "seizoen": 2026,
  "bijgewerkt": "2026-10-20T23:46:56+02:00",
  "groepen": [
    {"naam": "League Two", "rijen": [
      {"pos": 1, "club": 1844, "naam": "Salford City", "g": 11, "w": 7, "gl": 1, "v": 3, "dv": 17, "dt": 11,
       "p": 22, "vorm": "WWWWW", "opm": "Promotion - League One"}
    ]}
  ]
}
```

- Eén groep per poule. Twee poules in één tabel van API-Football (Derde Divisie A en B) worden gesplitst; ze
  hebben dan dezelfde `naam`. Periodestanden (Eerste Divisie) staan er niet in.
- `g` gespeeld, `w` gewonnen, `gl` gelijk, `v` verloren, `dv`/`dt` doelpunten voor/tegen, `p` punten (die van
  API-Football, dus met eventuele puntenaftrek), `vorm` de laatste uitslagen, `opm` de omschrijving van de plek
  (promotie, degradatie). Lege velden ontbreken.

# weddies-data

Wedstrijddata voor de Weddies-app. Een GitHub Action haalt de wedstrijden van gisteren, vandaag en morgen op bij
[API-Football](https://www.api-football.com) en zet via GitHub Pages alleen online wat de app nodig heeft, in een
eigen compact formaat:

- `index.json`: wanneer wat is bijgewerkt (en de API-tellerstand)
- `wedstrijden/<yyyy-mm-dd>.json`: de wedstrijden van die dag (alleen de competities uit `competities.json`)
- `seizoen.json`: clubs per competitie in het huidige seizoen

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

`seed/` bevat een paar wedstrijddagen in hetzelfde formaat als startset voor `seizoen.json`.

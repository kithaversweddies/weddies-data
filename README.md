# weddies-data

Wedstrijddata voor de Weddies-app. Een GitHub Action haalt de wedstrijden van gisteren, vandaag en morgen op bij
[API-Football](https://www.api-football.com) en zet ze via GitHub Pages online:

- `index.json`: wanneer wat is bijgewerkt
- `fixtures/<yyyy-mm-dd>.json`: de wedstrijden van die dag (alleen de competities uit `competities.json`)

De API-sleutel staat als GitHub Secret `API_FOOTBALL_KEY` in deze repository en zit niet in de app.
Schema en rekensom: zie `.github/workflows/wedstrijden.yml`.

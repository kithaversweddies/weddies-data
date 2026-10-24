# weddies-data

Wedstrijddata voor de Weddies-app. Een GitHub Action haalt het hele seizoen van de gevolgde competities op bij
[API-Football](https://www.api-football.com) (Pro-plan) en zet via GitHub Pages alleen online wat de app nodig heeft,
in een eigen compact formaat:

- `index.json`: wanneer wat is bijgewerkt (en de API-tellerstand volgens API-Football)
- `wedstrijden/<yyyy-mm-dd>.json`: de wedstrijden van die dag, van 7 dagen terug tot 60 dagen vooruit
- `competities/<id>.json`: het hele seizoen van één competitie (zelfde formaat; `datum` is dan het seizoensjaar)
- `seizoen.json`: clubs per competitie in het huidige seizoen (met groepen, bv. Derde Divisie zaterdag)
- `standen/<id>.json`: de stand van één competitie, per poule (zie hieronder)
- `details/<yyyy-mm-dd>.json`: per dag wie er speelde en scoorde (zie hieronder). De volledige versie staat in deze
  repo (`details/`): de Action zet die na het ophalen terug met een commit
- `wedstrijd/<id>.json`: alles van één gespeelde wedstrijd, voor het wedstrijdscherm (zie hieronder)
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

## Formaat van `details/<datum>.json` (formaat 1)

```json
{
  "formaat": 1,
  "datum": "2026-10-18",
  "wedstrijden": {
    "1552198": {"opst": true, "pogingen": 1, "op": "2026-10-24T17:40:00+02:00",
                "spelers": [[128765, "Søren Tengstedt", 410, 1], [150001, "J. Jansen", 410, 0]],
                "goals": [[128765, "Søren Tengstedt", 410, 7, "N"]]}
  }
}
```

- `spelers`: [speler-ID, naam, club-ID, 1 = basis / 0 = ingevallen]. Wisselspelers die niet invielen staan er niet in.
- `goals`: [speler-ID, naam, club-ID, minuut, soort]: `N` gewoon, `P` penalty, `E` eigen doelpunt. Gemiste
  penalty's en de strafschoppenreeks staan er niet in.
- `opst`: of er een opstelling was. Bij amateurs ontbreekt die bij API-Football bijna altijd; doelpunten met naam
  vaak niet. Zonder opstelling of met minder doelpunten dan de uitslag wordt het nog tot 3 keer opnieuw geprobeerd,
  steeds een dag later.

## Formaat van `wedstrijd/<id>.json` (formaat 1)

Zelfde velden als een wedstrijd in `details/`, plus (alleen als ze er zijn):

```json
{
  "formaat": 1, "id": 1552198, "v": 2, "opst": true, "rust": [0, 1],
  "goals": [[37438, "Sven Mijnans", 197, 21, "N", null]],
  "kaarten": [[37438, "Sven Mijnans", 197, 38, null, "G"]],
  "wissels": [[40, 197, "Guus Til", "Mikkel Bro Hansen"]],
  "opstellingen": [{"club": 198, "formatie": "5-3-2", "trainer": "R. Peter",
                    "basis": [[1, "K. Nikiéma", "G"]], "invallers": [[7, "D. van Mieghem", "F"]]}],
  "stats": {"198": {"balbezit": "53%", "schoten": 14, "opDoel": 2, "hoekschoppen": 3, "overtredingen": 20,
                    "buitenspel": 1, "geel": 3, "rood": 0, "reddingen": 3, "passes": 477, "xg": "1.43"}}
}
```

- `goals`: […, minuut, soort, extra minuut]; `kaarten`: [speler-ID, naam, club-ID, minuut, extra, `G`/`GR`/`R`]
  (geel, tweede geel, rood); `wissels`: [minuut, club-ID, eruit, erin]; `opstellingen`: [rugnummer, naam, positie].
- Wat API-Football niet heeft, ontbreekt (bij amateurs vaak alles behalve de ruststand). Alleen wedstrijden met
  iets erin krijgen een bestand.

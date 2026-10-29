#!/usr/bin/env python3
"""
Weddies – wedstrijden ophalen voor de app (draait als GitHub Action), op het Pro-plan van API-Football
(7.500 calls per dag, 300 per minuut, huidig seizoen beschikbaar).

Schrijft naar site/ (dat wordt via GitHub Pages online gezet), alles in Weddies' eigen compacte formaat
(zie compact() en README.md):

  site/index.json                     wanneer wat is bijgewerkt + API-tellerstand
  site/wedstrijden/<yyyy-mm-dd>.json  alle wedstrijden van die dag, van 7 dagen terug tot 60 dagen vooruit
  site/competities/<id>.json          het hele seizoen van één competitie (voor de clubpagina)
  site/seizoen.json                   clubs per competitie in het huidige seizoen (voor het paspoort)
  site/standen/<id>.json              de stand van één competitie, per poule (/standings, voor de clubpagina
                                      en de positie op het wedstrijdkaartje)
  site/details/<yyyy-mm-dd>.json      per dag wie er speelde en scoorde (klein, voor "Meest gezien" in de app)
  site/wedstrijd/<id>.json            alles van één gespeelde wedstrijd: doelpunten, kaarten, wissels, opstellingen
                                      en statistieken (voor het wedstrijdscherm in de app). Beide gemaakt uit
                                      details/ in deze repo, zie fetch_details()
  site/privacy.html, site/support.html  vaste pagina's uit pagina/ (privacybeleid en support voor de App Store)

Modi (MODE):
- 'seizoen' (elke 3 uur): per competitie het hele seizoen ophalen (/fixtures?league=..&season=..) plus één
  call voor de seizoensjaren (/leagues?current=true). ±101 calls (100 competities). Hieruit worden alle
  bestanden gemaakt. Daarna de stand (/standings) van elke competitie waar in de laatste uren een wedstrijd
  klaar is (of die nog geen stand heeft): op speeldagen dus een paar keer per dag, doordeweeks bijna niets.
  ±0–100 calls. Daarna opstellingen en doelpunten (/fixtures?ids=, 20 wedstrijden per call) van wedstrijden die
  sinds de vorige keer gespeeld zijn: ±0–25 calls, en het begin van het seizoen in stukjes van hooguit
  MAX_DETAIL_CALLS per run.
- 'live' (elke 5 minuten overdag): alleen vandaag (en net na middernacht gisteren) (/fixtures?date=..), en alleen
  als er iets bezig is of binnen 15 minuten begint. 1 call per dag. Zonder eerdere seizoensgegevens (eerste run)
  doet hij 'seizoen'.
- 'alles': handmatig, zelfde als 'seizoen'. 'herbouw': niets ophalen, alleen opnieuw neerzetten (0 calls).

De vorige versie van de website (competities/<id>.json) is het geheugen tussen runs: GitHub Pages wordt elke
keer helemaal vervangen. Er wordt alleen opnieuw neergezet als de inhoud echt veranderde. Is de vorige site
tijdelijk niet (helemaal) te lezen, dan stopt de run zonder iets neer te zetten (zie previous_site).

Omgevingsvariabelen: API_FOOTBALL_KEY (GitHub Secret), PAGES_URL, MODE, SITE_DIR.
"""

import hashlib
import html
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

API = "https://v3.football.api-sports.io"
TZ = ZoneInfo("Europe/Amsterdam")
# Grenzen. Eerst was er ook een daggrens MAX_CALLS = 2500, geteld vanuit `calls` in de vorige index.json. Die index
# gaat alleen online als de inhoud verandert, dus runs zonder wijziging (ook seizoensruns van ±101 calls) telden
# niet mee en de grens deed niets (issue #4). Nu telt alleen wat API-Football zelf meldt, plus een grens per run
# tegen een script dat op hol slaat. Normaal gebruik: ±800–1.300 per dag (zie de rekensom in de workflow).
MIN_REMAINING = 1500    # stoppen als API-Football minder dan dit over meldt: reserve voor stadions.py en handwerk
MAX_CALLS_PER_RUN = 400 # een seizoensrun met alle standen is ±200
PER_MINUTE = 300        # limiet van het Pro-plan; het script blijft ruim onder de helft
CALL_PAUSE = 0.4        # seconden tussen calls (≈150 per minuut)
DAYS_BACK = 7           # zoveel dagen terug staan online
DAYS_AHEAD = 60         # zoveel dagen vooruit staan online (in de app: 7 gratis, tot 60 met Weddies Plus)
LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
DONE = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
PLAYED = {"FT", "AET", "PEN", "AWD", "WO"}  # telt mee in de stand (uitgesteld, afgelast en gestaakt niet)
STANDINGS_AFTER = timedelta(hours=2)  # een wedstrijd telt zoveel na de aftrap als klaar
STANDINGS_FOLLOW_UP = timedelta(hours=6)  # en de stand wordt daarna nog zo lang bij elke seizoensrun ververst
# Netwerk- en leesfouten. Eerst alleen URLError/TimeoutError/JSONDecodeError; een ConnectionResetError of
# IncompleteRead tijdens resp.read() liet dan de hele run crashen (issue #5). OSError omvat URLError en
# TimeoutError, ValueError omvat JSONDecodeError.
NET_ERRORS = (OSError, ValueError, http.client.HTTPException)
FORMAAT = 1             # versie van het compacte formaat; ophogen bij een verandering die oude apps breekt

# Opstellingen en doelpunten (fetch_details)
DETAIL_IDS_PER_CALL = 20     # /fixtures?ids= geeft er hooguit 20 per call, mét opstellingen en gebeurtenissen
MAX_DETAIL_CALLS = 60        # per run; het hele seizoen tot nu toe (±10.000 wedstrijden) duurt zo een dag of twee
DETAIL_AFTER = timedelta(hours=3)   # zo lang na de aftrap is een wedstrijd (met opstellingen) compleet
DETAIL_RETRIES = 3           # zonder opstelling of doelpunten nog zo vaak opnieuw proberen…
DETAIL_RETRY_AFTER = timedelta(hours=24)   # …met een dag ertussen (API-Football vult soms later aan)
DETAIL_VERSION = 2           # 2: ook kaarten, wissels, opstellingen en statistieken (wedstrijdscherm, 2026-10-24).
                             # Oudere wedstrijden worden in de volgende runs opnieuw opgehaald, nieuwste eerst.
# Statistieken die het wedstrijdscherm toont (API-Football-naam → eigen sleutel)
STATS = {"Ball Possession": "balbezit", "Total Shots": "schoten", "Shots on Goal": "opDoel",
         "Corner Kicks": "hoekschoppen", "Fouls": "overtredingen", "Offsides": "buitenspel",
         "Yellow Cards": "geel", "Red Cards": "rood", "Goalkeeper Saves": "reddingen", "Total passes": "passes",
         "expected_goals": "xg"}

ROOT = Path(__file__).resolve().parent.parent
DETAILS = ROOT / "details"   # in de repo zelf: zo hoeft een run ze niet van de vorige site te halen
SITE = Path(os.environ.get("SITE_DIR", ROOT / "site"))
LEAGUES = set(json.loads((ROOT / "competities.json").read_text())["competities"])


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "weddies-data", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8")), resp.headers


class LimitReached(RuntimeError):
    """De call-grens is bereikt: stoppen met ophalen (niet doorgaan met de volgende competitie)."""


class Api:
    """API-Football met tellerstand, pauze tussen calls en harde grenzen."""

    def __init__(self, key):
        self.key, self.calls, self.remaining, self.limit, self.last = key, 0, None, None, 0.0

    def check_status(self):
        """Actuele tellerstand bij API-Football (/status telt zelf niet mee voor de limiet)."""
        data, _ = get_json(f"{API}/status", {"x-apisports-key": self.key})
        req = (data.get("response") or {}).get("requests") or {}
        if "limit_day" in req:
            self.limit = req["limit_day"]
            self.remaining = req["limit_day"] - req.get("current", 0)
            print(f"API-Football: {req.get('current')} van {req['limit_day']} calls vandaag gebruikt "
                  f"({(data['response'].get('subscription') or {}).get('plan')})")

    def used_today(self):
        """Calls vandaag volgens API-Football (alle gebruik van de sleutel, ook stadions.py), of None."""
        if self.limit is None or self.remaining is None:
            return None
        return self.limit - self.remaining

    def can_call(self):
        return self.calls < MAX_CALLS_PER_RUN and (self.remaining is None or self.remaining >= MIN_REMAINING)

    def get(self, endpoint, **params):
        if not self.can_call():
            raise LimitReached(f"grens bereikt ({self.calls} calls deze run, nog {self.remaining} over vandaag)")
        wait = CALL_PAUSE - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        url = f"{API}/{endpoint}?{urllib.parse.urlencode(params)}"
        data, headers = get_json(url, {"x-apisports-key": self.key})
        self.last = time.time()
        self.calls += 1
        left = headers.get("x-ratelimit-requests-remaining")
        if left and left.isdigit():
            self.remaining = int(left)
        if data.get("errors"):
            raise RuntimeError(f"API-Football: {data['errors']}")
        return data


class SiteUnavailable(Exception):
    """De vorige site is tijdelijk niet (helemaal) te lezen: deze run zet niets neer."""


def previous_site(pages_url):
    """De vorige versie van de website: index, het seizoen per competitie en de standen.

    Alleen een 404 op index.json betekent "nog geen site" (eerste run). Eerst gold elke fout (503, timeout) als
    "bestaat niet": dan deed een live-run een hele seizoensrun (±200 calls), of verdween één competitie van de
    site (issue #2). Nu stopt de run bij elke andere fout, en ook als een bestand dat index.json noemt er niet is
    (dan klopt de site niet en zou een run er data mee wissen)."""
    if not pages_url:
        return {}, {}, {}
    stamp = int(datetime.now().timestamp())

    def fetch(path):
        try:
            return get_json(f"{pages_url}/{path}?t={stamp}")[0]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise SiteUnavailable(f"{path}: HTTP {e.code}") from e
        except NET_ERRORS as e:
            raise SiteUnavailable(f"{path}: {e!r}") from e

    def required(path):
        data = fetch(path)
        if data is None:
            raise SiteUnavailable(f"{path} staat in index.json maar geeft 404")
        return data

    index = fetch("index.json") or {}
    leagues = {int(lid): required(f"competities/{lid}.json") for lid in index.get("competities", {})}
    standings = {int(lid): required(f"standen/{lid}.json") for lid in index.get("standen", {})}
    return index, leagues, standings


def compact(api_data, day):
    """API-Football-antwoord -> Weddies' eigen formaat: alleen wat de app en stadions.py gebruiken.
    Logo's staan er niet in; die liggen vast per ID (media.api-sports.io/football/teams|leagues/<id>.png).
    Lege velden (geen minuut, stand, stadion of plaats) worden weggelaten."""
    comps, matches = {}, []
    for f in api_data.get("response", []):
        fx, lg, teams, goals = f["fixture"], f["league"], f["teams"], f.get("goals") or {}
        comps.setdefault(str(lg["id"]), {"naam": lg["name"], "land": lg["country"], "seizoen": lg.get("season")})
        m = {"id": fx["id"], "competitie": lg["id"], "ronde": lg.get("round"), "aftrap": fx["date"],
             "status": fx["status"]["short"], "minuut": fx["status"].get("elapsed"),
             "thuis": {"id": teams["home"]["id"], "naam": html.unescape(teams["home"]["name"])},
             "uit": {"id": teams["away"]["id"], "naam": html.unescape(teams["away"]["name"])}}
        if goals.get("home") is not None or goals.get("away") is not None:
            m["stand"] = [goals.get("home"), goals.get("away")]
        venue = fx.get("venue") or {}
        m["stadion"] = html.unescape(venue.get("name") or "") or None
        m["plaats"] = html.unescape(venue.get("city") or "") or None
        matches.append({k: v for k, v in m.items() if v is not None})
    matches.sort(key=lambda m: (m["aftrap"], m["id"]))
    return {"formaat": FORMAAT, "datum": day, "competities": comps, "wedstrijden": matches}


def build_season(league_files):
    """Clubs per competitie, opgebouwd uit het hele speelschema. Omdat alle wedstrijden van het seizoen bekend
    zijn, staan alle clubs erin; split_groups() verdeelt competities als de Derde Divisie in groepen."""
    comps = {}
    for data in league_files:
        for m in data.get("wedstrijden", []):
            lg = data["competities"][str(m["competitie"])]
            comp = comps.setdefault(str(m["competitie"]), {"naam": lg["naam"], "land": lg["land"],
                                                            "seizoen": lg.get("seizoen"), "clubs": {}, "rondes": {},
                                                            "paren": {}})
            home, away = m["thuis"], m["uit"]
            comp["clubs"][str(home["id"])] = home["naam"]
            comp["clubs"][str(away["id"])] = away["naam"]
            rnd = m.get("ronde") or "?"
            comp["rondes"][rnd] = sorted(set(comp["rondes"].get(rnd, [])) | {home["id"], away["id"]})
            comp["paren"][str(m["id"])] = [home["id"], away["id"], datetime.fromisoformat(m["aftrap"]).weekday()]
    for comp in comps.values():
        comp["groepen"] = split_groups(comp)
        del comp["rondes"], comp["paren"]  # alleen nodig om de groepen te bepalen; de app gebruikt ze niet
    return {"competities": comps}


def is_complete(ids, rounds):
    """Compleet als in minstens twee speelronden alle clubs (op hooguit één na) speelden."""
    n = len(ids)
    full = sum(1 for r in rounds.values() if len(ids & set(r)) >= n - 1)
    return n >= 8 and full >= 2


def split_groups(comp):
    """Sommige competities (Derde Divisie) bestaan uit groepen die nooit tegen elkaar spelen; die zitten bij
    API-Football in één competitie. Clubs die (via-via) tegen elkaar speelden vormen één groep."""
    parent = {int(c): int(c) for c in comp["clubs"]}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for home, away, _ in comp.get("paren", {}).values():
        parent[root(home)] = root(away)
    members = {}
    for c in parent:
        members.setdefault(root(c), set()).add(c)
    big = [m for m in members.values() if len(m) >= 8]
    all_ids = {int(c) for c in comp["clubs"]}

    # Alleen splitsen als de clubs duidelijk in 2 of 3 grote groepen uiteenvallen
    if not (2 <= len(big) <= 3 and sum(len(m) for m in big) == len(all_ids)):
        return [{"naam": comp["naam"], "clubs": sorted(all_ids), "compleet": is_complete(all_ids, comp["rondes"]),
                 "zeker": len(members) == 1 or len(all_ids) < 8}]
    groups = []
    for i, ids in enumerate(sorted(big, key=lambda m: min(m)), 1):
        days = [d for h, a, d in comp["paren"].values() if h in ids]
        share = {d: days.count(d) / len(days) for d in set(days)} if days else {}
        label = f"groep {i}"
        if share.get(5, 0) >= 0.7:
            label = "zaterdag"
        elif share.get(6, 0) >= 0.7:
            label = "zondag"
        groups.append({"naam": f"{comp['naam']} ({label})", "clubs": sorted(ids),
                       "compleet": is_complete(ids, comp["rondes"]), "zeker": True})
    return groups


def fetch_season(api, leagues, seasons):
    """Het hele seizoen van elke gevolgde competitie. Vult {competitie-ID: compact bestand} en {ID: seizoensjaar}.
    Geeft de ID's terug die echt opgehaald zijn: alleen die krijgen een nieuwe stempel in index.json (eerst kreeg
    elke competitie de stempel van nu, ook als het ophalen mislukte en de oude data bleef staan; issue #8)."""
    ok = set()
    for item in api.get("leagues", current="true")["response"]:
        lid = item["league"]["id"]
        if lid in LEAGUES:
            seasons[lid] = next((s["year"] for s in item["seasons"] if s.get("current")), None)
    for lid in sorted(LEAGUES):
        year = seasons.get(lid)
        if year is None:
            print(f"competitie {lid}: geen huidig seizoen bij API-Football")
            continue
        try:
            raw = api.get("fixtures", league=lid, season=year, timezone="Europe/Amsterdam")
        except LimitReached as e:
            # Stoppen, net als fetch_standings; eerst volgde er per resterende competitie een foutregel (issue #9)
            print(f"seizoen: gestopt bij competitie {lid} ({e})")
            break
        except (RuntimeError, *NET_ERRORS) as e:
            print(f"competitie {lid}: niet gelukt ({e!r})")
            continue
        file = compact({"response": raw.get("response", [])}, str(year))
        if not file["competities"]:  # nog geen wedstrijden: naam en land uit /leagues halen we hier niet op
            continue
        leagues[lid] = file
        ok.add(lid)
    return ok


def compact_standings(raw, lid, year, stamp):
    """/standings -> Weddies' eigen formaat. Eén lijst per poule ('groep'): bij competities met meerdere poules
    (bijv. de Regionalliga's of de Derde Divisie A/B) geeft API-Football meerdere tabellen. De punten zijn die van
    API-Football (met eventuele puntenaftrek), niet zelf berekend."""
    groups = []
    for item in raw.get("response", []):
        for table in (item.get("league") or {}).get("standings") or []:
            rows = []
            for r in table:
                al = r.get("all") or {}
                goals = al.get("goals") or {}
                row = {"pos": r.get("rank"), "club": (r.get("team") or {}).get("id"),
                       "naam": html.unescape((r.get("team") or {}).get("name") or ""),
                       "g": al.get("played"), "w": al.get("win"), "gl": al.get("draw"), "v": al.get("lose"),
                       "dv": goals.get("for"), "dt": goals.get("against"), "p": r.get("points"),
                       "vorm": r.get("form") or None, "opm": r.get("description") or None}
                rows.append({k: v for k, v in row.items() if v is not None})
            name = html.unescape(table[0].get("group") or "") if table else ""
            # Periodestanden (Eerste Divisie: 'Period 1' t/m 4) niet: alleen de echte stand
            if not rows or "period" in name.lower():
                continue
            # Twee poules in één tabel (Derde Divisie A en B: posities 1–18 en dan weer 1–18): splitsen. Met `<`,
            # niet `<=`: twee clubs met dezelfde rang zijn geen nieuwe poule (issue #7)
            part = [rows[0]]
            for row in rows[1:]:
                if row.get("pos", 0) < part[-1].get("pos", 0):
                    groups.append({"naam": name, "rijen": part})
                    part = []
                part.append(row)
            groups.append({"naam": name, "rijen": part})
    return {"formaat": FORMAAT, "competitie": lid, "seizoen": year, "bijgewerkt": stamp, "groepen": groups}


def standings_due(lid, file, standings, now):
    """Stand opnieuw ophalen? Als er nog geen is, of als er een gespeelde wedstrijd klaar is en de vorige stand
    van vóór of kort na dat moment is. Een wedstrijd telt als klaar ±2 uur na de aftrap.

    Eerst werd de stand maar één keer na zo'n wedstrijd opgehaald. API-Football werkt de stand soms pas later
    bij, en dan bleef een oude stand dagen staan (League Two miste een hele speelronde; issue #1). Nu wordt hij
    nog STANDINGS_FOLLOW_UP lang bij elke seizoensrun ververst (±2 extra calls per competitie per speeldag).
    Uitgesteld, afgelast en gestaakt tellen niet: de stand verandert dan niet."""
    old = standings.get(lid)
    if not old or not old.get("bijgewerkt"):
        return True
    last = datetime.fromisoformat(old["bijgewerkt"])
    for m in file["wedstrijden"]:
        if m["status"] in PLAYED:
            done_at = datetime.fromisoformat(m["aftrap"]) + STANDINGS_AFTER
            if done_at <= now and last < done_at + STANDINGS_FOLLOW_UP:
                return True
    return False


def fetch_standings(api, leagues, seasons, standings, now):
    """De stand van elke competitie waarvoor dat nodig is (zie standings_due)."""
    fetched = 0
    for lid in sorted(leagues):
        year = seasons.get(lid)
        if year is None or not standings_due(lid, leagues[lid], standings, now):
            continue
        try:
            raw = api.get("standings", league=lid, season=year)
        except LimitReached as e:
            print(f"standen: gestopt bij {lid} ({e})")
            break
        except (RuntimeError, *NET_ERRORS) as e:
            print(f"stand {lid}: niet gelukt ({e!r})")
            continue
        file = compact_standings(raw, lid, year, now.isoformat(timespec="seconds"))
        if file["groepen"]:  # geen stand (nog geen wedstrijden): de vorige laten staan
            standings[lid] = file
            fetched += 1
    print(f"standen: {fetched} opgehaald")


def compact_details(fixture):
    """Eén wedstrijd van /fixtures?ids= -> wat de app nodig heeft voor "Meest gezien".

    - `spelers`: [speler-ID, naam, club-ID, 1 = basis / 0 = ingevallen]. Een wissel staat bij API-Football als
      gebeurtenis "subst" met `player` = wie eruit gaat en `assist` = wie erin komt (nagekeken 2026-10-24); een
      wisselspeler die niet inviel, telt niet.
    - `goals`: [speler-ID, naam, club-ID, minuut, "N" gewoon / "P" penalty / "E" eigen doelpunt]. Gemiste
      penalty's niet.
    - `opst`: of er een opstelling was. Bij amateurs (Derde Divisie, Serie D, Tercera, Oberliga) ontbreekt die bijna
      altijd; doelpunten met naam staan er vaak wel.
    Sinds versie 2 (`v`) ook voor het wedstrijdscherm (zie compact_match): `goals` met de extra minuut als zesde
    waarde, `kaarten`, `wissels`, `opstellingen`, `stats` en `rust` (ruststand). Lege onderdelen ontbreken."""
    lineups = fixture.get("lineups") or []
    events = fixture.get("events") or []
    came_on = {((e.get("assist") or {}).get("id")) for e in events if e.get("type") == "subst"}
    players = []
    for team in lineups:
        club = (team.get("team") or {}).get("id")
        for item in team.get("startXI") or []:
            pl = item.get("player") or {}
            if pl.get("id"):
                players.append([pl["id"], html.unescape(pl.get("name") or ""), club, 1])
        for item in team.get("substitutes") or []:
            pl = item.get("player") or {}
            if pl.get("id") and pl["id"] in came_on:
                players.append([pl["id"], html.unescape(pl.get("name") or ""), club, 0])
    goals = []
    for e in events:
        # Gemiste penalty's en de strafschoppenreeks tellen niet (die staan ook als "Goal")
        if e.get("type") != "Goal" or e.get("detail") == "Missed Penalty" or e.get("comments") == "Penalty Shootout":
            continue
        pl = e.get("player") or {}
        kind = {"Penalty": "P", "Own Goal": "E"}.get(e.get("detail"), "N")
        goals.append([pl.get("id"), html.unescape(pl.get("name") or ""), (e.get("team") or {}).get("id"),
                      (e.get("time") or {}).get("elapsed"), kind, (e.get("time") or {}).get("extra")])
    item = {"v": DETAIL_VERSION, "opst": bool(players), "spelers": players, "goals": goals}

    # Kaarten: [speler-ID, naam, club-ID, minuut, extra, "G" geel / "GR" tweede geel / "R" rood]
    cards = []
    for e in events:
        if e.get("type") != "Card":
            continue
        detail = (e.get("detail") or "").lower()
        kind = "GR" if "second" in detail else "R" if "red" in detail else "G"
        pl = e.get("player") or {}
        cards.append([pl.get("id"), html.unescape(pl.get("name") or ""), (e.get("team") or {}).get("id"),
                      (e.get("time") or {}).get("elapsed"), (e.get("time") or {}).get("extra"), kind])
    # Wissels: [minuut, club-ID, eruit, erin]
    subs = [[(e.get("time") or {}).get("elapsed"), (e.get("team") or {}).get("id"),
             html.unescape((e.get("player") or {}).get("name") or ""),
             html.unescape((e.get("assist") or {}).get("name") or "")]
            for e in events if e.get("type") == "subst"]
    # Opstellingen: per club de formatie, de trainer, de basis en de wisselspelers die invielen
    # ([nr, naam, positie, speler-ID]; het ID sinds 2026-10-28 voor de spelerspagina in de app)
    teams = []
    for team in lineups:
        def row(item):
            pl = item.get("player") or {}
            return [pl.get("number"), html.unescape(pl.get("name") or ""), pl.get("pos"), pl.get("id")]
        start = [row(i) for i in team.get("startXI") or []]
        if not start:
            continue
        teams.append({k: v for k, v in {
            "club": (team.get("team") or {}).get("id"),
            "formatie": team.get("formation"),
            "trainer": html.unescape((team.get("coach") or {}).get("name") or "") or None,
            "basis": start,
            "invallers": [row(i) for i in team.get("substitutes") or []
                          if (i.get("player") or {}).get("id") in came_on],
        }.items() if v})
    # Statistieken per club, alleen wat er is
    stats = {}
    for team in fixture.get("statistics") or []:
        club = (team.get("team") or {}).get("id")
        values = {STATS[x["type"]]: x["value"] for x in team.get("statistics") or []
                  if x.get("type") in STATS and x.get("value") is not None}
        if club and values:
            stats[str(club)] = values
    half = (fixture.get("score") or {}).get("halftime") or {}
    for key, value in (("kaarten", cards), ("wissels", subs), ("opstellingen", teams), ("stats", stats)):
        if value:
            item[key] = value
    if half.get("home") is not None and half.get("away") is not None:
        item["rust"] = [half["home"], half["away"]]
    return item


def with_lineup_ids(item):
    """Opstellingen van vóór 2026-10-28 hebben geen speler-ID ([nr, naam, positie]). Zonder nieuwe calls aanvullen
    uit `spelers` (zelfde bron, zelfde naam en club), zodat ook oudere wedstrijden naar de spelerspagina linken."""
    if not item.get("opstellingen"):
        return item
    ids = {}
    for pid, name, club, *_ in item.get("spelers") or []:
        ids.setdefault((club, name), pid)
    teams = []
    for team in item["opstellingen"]:
        def fill(row, club=team.get("club")):
            if len(row) >= 4 and row[3]:
                return row
            return [*row[:3], ids.get((club, row[1]))]
        teams.append({**team, **{k: [fill(r) for r in team[k]] for k in ("basis", "invallers") if team.get(k)}})
    return {**item, "opstellingen": teams}


def slim_details(file):
    """Het dagbestand voor "Meest gezien": alleen wie er speelde en scoorde (de rest staat in wedstrijd/<id>.json)."""
    keep = ("opst", "spelers", "goals", "pogingen", "op", "v")
    return {**file, "wedstrijden": {fid: {k: item[k] for k in keep if k in item}
                                    for fid, item in file.get("wedstrijden", {}).items()}}


def load_details():
    """Alle eerder opgehaalde opstellingen en doelpunten uit details/: {datum: {"wedstrijden": {id: …}}}."""
    out = {}
    for path in sorted(DETAILS.glob("*.json")):
        try:
            out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            print(f"::warning::details/{path.name} niet te lezen ({e!r}); wordt opnieuw opgebouwd")
    return out


def details_due(match, stored, now):
    """Opstelling en doelpunten (opnieuw) ophalen? Alleen voor gespeelde wedstrijden, ±3 uur na de aftrap. Zonder
    opstelling of zonder alle doelpunten nog een paar keer, een dag later (lagere divisies vult API-Football soms
    later aan)."""
    if match["status"] not in ("FT", "AET", "PEN"):
        return False
    kickoff = datetime.fromisoformat(match["aftrap"])
    if kickoff + DETAIL_AFTER > now:
        return False
    if stored is None:
        return True
    # Opgehaald vóór het wedstrijdscherm (zonder kaarten, opstellingen en statistieken): nog één keer
    if stored.get("v", 1) < DETAIL_VERSION:
        return True
    complete = stored.get("opst") and len(stored.get("goals", [])) == sum(match.get("stand") or [0, 0])
    if complete or stored.get("pogingen", 1) >= DETAIL_RETRIES:
        return False
    last = datetime.fromisoformat(stored["op"]) if stored.get("op") else kickoff
    return last + DETAIL_RETRY_AFTER <= now


def fetch_details(api, leagues, details, now):
    """Opstellingen en doelpunten van gespeelde wedstrijden, 20 per call (/fixtures?ids=), nieuwste eerst. Geeft
    het aantal opgehaalde wedstrijden terug."""
    todo = []
    for file in leagues.values():
        for m in file["wedstrijden"]:
            day = m["aftrap"][:10]
            stored = details.get(day, {}).get("wedstrijden", {}).get(str(m["id"]))
            if details_due(m, stored, now):
                todo.append(m)
    todo.sort(key=lambda m: m["aftrap"], reverse=True)
    print(f"details: {len(todo)} wedstrijden te doen")
    stamp = now.isoformat(timespec="seconds")
    done = 0
    for start in range(0, min(len(todo), MAX_DETAIL_CALLS * DETAIL_IDS_PER_CALL), DETAIL_IDS_PER_CALL):
        batch = todo[start:start + DETAIL_IDS_PER_CALL]
        try:
            raw = api.get("fixtures", ids="-".join(str(m["id"]) for m in batch), timezone="Europe/Amsterdam")
        except LimitReached as e:
            print(f"details: gestopt ({e})")
            break
        except (RuntimeError, *NET_ERRORS) as e:
            print(f"details: niet gelukt ({e!r})")
            continue
        found = {f["fixture"]["id"]: f for f in raw.get("response", [])}
        for m in batch:
            day = m["aftrap"][:10]
            file = details.setdefault(day, {"formaat": FORMAAT, "datum": day, "wedstrijden": {}})
            old = file["wedstrijden"].get(str(m["id"]))
            item = compact_details(found[m["id"]]) if m["id"] in found else {"v": DETAIL_VERSION, "opst": False,
                                                                              "spelers": [], "goals": []}
            # Bij het opnieuw ophalen voor versie 2 telt de oude poging niet mee
            item["pogingen"] = ((old or {}).get("pogingen", 0) if (old or {}).get("v", 1) >= DETAIL_VERSION else 0) + 1
            item["op"] = stamp
            file["wedstrijden"][str(m["id"])] = item
            done += 1
    print(f"details: {done} wedstrijden opgehaald")
    return done


def all_matches(leagues):
    """{wedstrijd-ID: (wedstrijd, competitie-info)} over alle competities."""
    out = {}
    for lid, file in leagues.items():
        info = file["competities"].get(str(lid))
        for m in file["wedstrijden"]:
            out[m["id"]] = (m, info)
    return out


def apply_live(leagues, day_file):
    """Live standen van één dag in de seizoensgegevens zetten (nieuwe wedstrijden erbij, bestaande bijwerken)."""
    changed = 0
    for m in day_file["wedstrijden"]:
        lid = m["competitie"]
        file = leagues.get(lid)
        if file is None:
            file = leagues[lid] = {"formaat": FORMAAT, "datum": str(day_file["competities"][str(lid)].get("seizoen")),
                                   "competities": {str(lid): day_file["competities"][str(lid)]}, "wedstrijden": []}
        for i, old in enumerate(file["wedstrijden"]):
            if old["id"] == m["id"]:
                if old != m:
                    file["wedstrijden"][i] = m
                    changed += 1
                break
        else:
            file["wedstrijden"].append(m)
            changed += 1
    return changed


def day_file(leagues, day):
    """Alle wedstrijden van één (Nederlandse) kalenderdag, uit de seizoensgegevens."""
    comps, matches = {}, []
    for lid, file in leagues.items():
        for m in file["wedstrijden"]:
            if m["aftrap"][:10] == day:
                matches.append(m)
                comps[str(lid)] = file["competities"][str(lid)]
    matches.sort(key=lambda m: (m["aftrap"], m["id"]))
    return {"formaat": FORMAAT, "datum": day, "competities": comps, "wedstrijden": matches}


def needs_live_refresh(today_file, now):
    """Deze dag ophalen? Alleen als er een wedstrijd bezig is, binnen 15 minuten begint of net klaar zou moeten
    zijn."""
    for m in today_file["wedstrijden"]:
        if m["status"] in LIVE:
            return True
        kickoff = datetime.fromisoformat(m["aftrap"])
        if m["status"] not in DONE and kickoff - timedelta(minutes=15) <= now <= kickoff + timedelta(hours=3):
            return True
    return False


def latest(stamps):
    """Het laatste van een paar ISO-tijdstempels (lege overgeslagen). Als datetime vergeleken, niet als tekst:
    rond de wintertijd staan er +02:00 en +01:00 door elkaar."""
    return max((s for s in stamps if s), key=datetime.fromisoformat, default="")


def oldest(stamps):
    """Het vroegste van een paar ISO-tijdstempels (lege overgeslagen), zie latest()."""
    return min((s for s in stamps if s), key=datetime.fromisoformat, default="")


def set_output(changed, details_changed=False):
    if gh_output := os.environ.get("GITHUB_OUTPUT"):
        with open(gh_output, "a") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
            # details/ is bijgewerkt: de workflow zet die bestanden terug in de repo (commit + push)
            f.write(f"details_changed={'true' if details_changed else 'false'}\n")


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    path.write_text(text, encoding="utf-8")
    return text


def main():
    key = os.environ.get("API_FOOTBALL_KEY", "")
    mode = os.environ.get("MODE", "alles")
    if not key and mode != "herbouw":
        sys.exit("API_FOOTBALL_KEY ontbreekt (GitHub Secret).")
    now = datetime.now(TZ)
    today = now.date()

    try:
        index, leagues, standings = previous_site(os.environ.get("PAGES_URL", "").rstrip("/"))
    except SiteUnavailable as e:
        # Nog geen call gedaan; de volgende run probeert het opnieuw. Geen rode run (geen mail bij elke 503).
        print(f"::warning::Vorige site niet te lezen ({e}); website blijft zoals hij is.")
        set_output(False)
        return
    utc_day = str(datetime.now(timezone.utc).date())
    api = Api(key)
    if mode != "herbouw":
        try:
            api.check_status()
        except NET_ERRORS as e:
            print(f"status niet op te vragen ({e!r})")
    fetched_leagues = dict(index.get("competities", {}))
    live_fetched = index.get("live")
    details = load_details()
    details_fetched = 0

    if mode == "live" and not leagues:
        mode = "seizoen"  # eerste run: eerst het hele seizoen
    try:
        if mode in ("seizoen", "alles"):
            before = len(leagues)
            seasons = {}
            ok = fetch_season(api, leagues, seasons)
            stamp = now.isoformat(timespec="seconds")
            fetched_leagues = {str(lid): stamp if lid in ok else fetched_leagues.get(str(lid), "")
                               for lid in leagues}
            print(f"seizoen: {len(ok)} van {len(leagues)} competities opgehaald ({len(leagues) - before} nieuw)")
            fetch_standings(api, leagues, seasons, standings, now)
            details_fetched = fetch_details(api, leagues, details, now)
        elif mode == "live":
            days = [today]
            if now.hour < 2:
                days.insert(0, today - timedelta(days=1))  # net na middernacht: gisteren afronden
            for d in days:
                # Ook voor gisteren: eerst werd gisteren na middernacht bij elke run opgehaald, ook als alles al
                # klaar was (issue #6)
                if not needs_live_refresh(day_file(leagues, str(d)), now):
                    print(f"live {d}: niets bezig, geen call")
                    continue
                raw = api.get("fixtures", date=str(d), timezone="Europe/Amsterdam")
                ours = {"response": [f for f in raw.get("response", []) if f["league"]["id"] in LEAGUES]}
                n = apply_live(leagues, compact(ours, str(d)))
                live_fetched = now.isoformat(timespec="seconds")
                print(f"live {d}: {len(ours['response'])} wedstrijden, {n} veranderd")
            # Een competitie die live voor het eerst opduikt, moet in de index staan: anders leest de volgende
            # run hem niet terug van de site
            for lid in leagues:
                fetched_leagues.setdefault(str(lid), live_fetched or "")
    except (RuntimeError, *NET_ERRORS) as e:
        print(f"gestopt: {e!r}")

    if not leagues:
        # Niets opgehaald en ook niets van de vorige site: de website niet leeg overschrijven
        print("Geen seizoensgegevens; website blijft zoals hij is.")
        set_output(False)
        sys.exit(1 if mode != "herbouw" else 0)

    # Alles opnieuw neerzetten uit de seizoensgegevens
    texts = []
    for lid, file in sorted(leagues.items()):
        file["wedstrijden"].sort(key=lambda m: (m["aftrap"], m["id"]))
        texts.append(write(SITE / "competities" / f"{lid}.json", file))
    window = [str(today + timedelta(days=i)) for i in range(-DAYS_BACK, DAYS_AHEAD + 1)]
    for day in window:
        texts.append(write(SITE / "wedstrijden" / f"{day}.json", day_file(leagues, day)))
    texts.append(write(SITE / "seizoen.json", build_season(leagues.values())))
    for lid, file in sorted(standings.items()):
        texts.append(write(SITE / "standen" / f"{lid}.json", file))
    # Opstellingen en doelpunten: alles in de repo (details/); op de site per dag een klein bestand (Meest gezien)
    # en per wedstrijd alles (wedstrijdscherm). Alleen wedstrijden met iets erin krijgen een eigen bestand.
    for day, file in sorted(details.items()):
        texts.append(write(SITE / "details" / f"{day}.json", slim_details(file)))
        for fid, item in file.get("wedstrijden", {}).items():
            if any(item.get(k) for k in ("goals", "opst", "kaarten", "stats", "rust")):
                texts.append(write(SITE / "wedstrijd" / f"{fid}.json",
                                   {"formaat": FORMAAT, "id": int(fid), **with_lineup_ids(item)}))
        if details_fetched:
            write(DETAILS / f"{day}.json", file)
    # Vaste pagina's (privacybeleid, support) uit pagina/: Pages wordt elke run helemaal vervangen, dus ze moeten
    # elke keer mee. Ze tellen mee voor `inhoud`, zodat een aangepaste pagina ook online komt.
    for page in sorted((ROOT / "pagina").glob("*.html")):
        text = page.read_text(encoding="utf-8")
        (SITE / page.name).write_text(text, encoding="utf-8")
        texts.append(text)
    content = hashlib.sha1("".join(texts).encode()).hexdigest()

    # Hoe vers elke dag is: de oudste competitie. Voor vandaag het laatste van dat en de live-call: eerst won de
    # live-call altijd, ook als die van gisteravond was en er daarna een seizoensrun was (issue #10)
    season_stamp = oldest(fetched_leagues.values())
    new_index = {
        "formaat": FORMAAT,
        "bijgewerkt": now.isoformat(timespec="seconds"),
        "dagen": {d: latest([season_stamp, live_fetched]) if d == str(today) else season_stamp for d in window},
        "competities": fetched_leagues,
        "standen": {str(lid): file.get("bijgewerkt") for lid, file in sorted(standings.items())},
        "details": sorted(details),
        "live": live_fetched,
        "calls_dag": utc_day,
        "calls": api.used_today() if api.used_today() is not None else index.get("calls"),  # volgens API-Football
        "calls_run": api.calls,
        "over": api.remaining,
        "inhoud": content,
        "bron": "API-Football (api-sports.io)",
    }
    (SITE / "index.json").write_text(json.dumps(new_index, indent=1), encoding="utf-8")

    changed = content != index.get("inhoud") or mode in ("alles", "herbouw")
    print(f"Modus {mode}: {api.calls} calls deze run, {api.used_today()} vandaag, nog {api.remaining} over, "
          f"{'nieuwe inhoud' if changed else 'niets veranderd'}")
    set_output(changed, details_changed=details_fetched > 0)


if __name__ == "__main__":
    main()

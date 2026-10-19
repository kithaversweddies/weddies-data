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

Modi (MODE):
- 'seizoen' (elke 3 uur): per competitie het hele seizoen ophalen (/fixtures?league=..&season=..) plus één
  call voor de seizoensjaren (/leagues?current=true). ±39 calls. Hieruit worden alle bestanden gemaakt.
  Daarna de stand (/standings) van elke competitie waar sinds de vorige stand een wedstrijd klaar is (of die
  nog geen stand heeft): op speeldagen dus een paar keer per dag, doordeweeks bijna niets. ±0–39 calls.
- 'live' (elke 5 minuten overdag): alleen vandaag (/fixtures?date=..), en alleen als er iets bezig is of
  binnen 15 minuten begint. 1 call. Zonder eerdere seizoensgegevens (eerste run) doet hij 'seizoen'.
- 'alles': handmatig, zelfde als 'seizoen'. 'herbouw': niets ophalen, alleen opnieuw neerzetten (0 calls).

De vorige versie van de website (competities/<id>.json) is het geheugen tussen runs: GitHub Pages wordt elke
keer helemaal vervangen. Er wordt alleen opnieuw neergezet als de inhoud echt veranderde.

Omgevingsvariabelen: API_FOOTBALL_KEY (GitHub Secret), PAGES_URL, MODE, SITE_DIR.
"""

import hashlib
import html
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
MAX_CALLS = 2500        # per dag voor de Action (van de 7.500); normaal gebruik is ±500
MIN_REMAINING = 1500    # stoppen als API-Football minder dan dit over meldt: reserve voor stadions.py en handwerk
PER_MINUTE = 300        # limiet van het Pro-plan; het script blijft ruim onder de helft
CALL_PAUSE = 0.4        # seconden tussen calls (≈150 per minuut)
DAYS_BACK = 7           # zoveel dagen terug staan online
DAYS_AHEAD = 60         # zoveel dagen vooruit staan online (in de app: 7 gratis, tot 60 met Weddies Plus)
LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
DONE = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
FORMAAT = 1             # versie van het compacte formaat; ophogen bij een verandering die oude apps breekt

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(os.environ.get("SITE_DIR", ROOT / "site"))
LEAGUES = set(json.loads((ROOT / "competities.json").read_text())["competities"])


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "weddies-data", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8")), resp.headers


class Api:
    """API-Football met tellerstand, pauze tussen calls en harde grenzen."""

    def __init__(self, key, calls, remaining):
        self.key, self.calls, self.remaining, self.last = key, calls, remaining, 0.0

    def check_status(self):
        """Actuele tellerstand bij API-Football (/status telt zelf niet mee voor de limiet)."""
        data, _ = get_json(f"{API}/status", {"x-apisports-key": self.key})
        req = (data.get("response") or {}).get("requests") or {}
        if "limit_day" in req:
            self.remaining = req["limit_day"] - req.get("current", 0)
            print(f"API-Football: {req.get('current')} van {req['limit_day']} calls vandaag gebruikt "
                  f"({(data['response'].get('subscription') or {}).get('plan')})")

    def can_call(self):
        return self.calls < MAX_CALLS and (self.remaining is None or self.remaining >= MIN_REMAINING)

    def get(self, endpoint, **params):
        if not self.can_call():
            raise RuntimeError(f"grens bereikt ({self.calls} calls vandaag, nog {self.remaining} over)")
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


def previous_site(pages_url):
    """De vorige versie van de website: index, het seizoen per competitie en de standen."""
    if not pages_url:
        return {}, {}, {}
    stamp = int(datetime.now().timestamp())

    def fetch(path):
        try:
            return get_json(f"{pages_url}/{path}?t={stamp}")[0]
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            return None

    index = fetch("index.json") or {}
    leagues = {}
    for lid in index.get("competities", {}):
        data = fetch(f"competities/{lid}.json")
        if data is not None:
            leagues[int(lid)] = data
    standings = {}
    for lid in index.get("standen", {}):
        data = fetch(f"standen/{lid}.json")
        if data is not None:
            standings[int(lid)] = data
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
    """Het hele seizoen van elke gevolgde competitie. Vult {competitie-ID: compact bestand} en {ID: seizoensjaar}."""
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
        except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
            print(f"competitie {lid}: niet gelukt ({e})")
            continue
        file = compact({"response": raw.get("response", [])}, str(year))
        if not file["competities"]:  # nog geen wedstrijden: naam en land uit /leagues halen we hier niet op
            continue
        leagues[lid] = file
    return leagues


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
            # Twee poules in één tabel (Derde Divisie A en B: posities 1–18 en dan weer 1–18): splitsen
            part = [rows[0]]
            for row in rows[1:]:
                if row.get("pos", 0) <= part[-1].get("pos", 0):
                    groups.append({"naam": name, "rijen": part})
                    part = []
                part.append(row)
            groups.append({"naam": name, "rijen": part})
    return {"formaat": FORMAAT, "competitie": lid, "seizoen": year, "bijgewerkt": stamp, "groepen": groups}


def standings_due(lid, file, standings, now):
    """Stand opnieuw ophalen? Als er nog geen is, of als sinds de vorige stand een wedstrijd klaar is. Een
    wedstrijd telt als klaar ±2 uur na de aftrap (API-Football werkt de stand pas na het eindsignaal bij)."""
    old = standings.get(lid)
    if not old or not old.get("bijgewerkt"):
        return True
    last = datetime.fromisoformat(old["bijgewerkt"])
    for m in file["wedstrijden"]:
        if m["status"] in DONE:
            done_at = datetime.fromisoformat(m["aftrap"]) + timedelta(hours=2)
            if last < done_at <= now:
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
        except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
            print(f"stand {lid}: niet gelukt ({e})")
            if isinstance(e, RuntimeError) and "grens" in str(e):
                break
            continue
        file = compact_standings(raw, lid, year, now.isoformat(timespec="seconds"))
        if file["groepen"]:  # geen stand (nog geen wedstrijden): de vorige laten staan
            standings[lid] = file
            fetched += 1
    print(f"standen: {fetched} opgehaald")


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
    """Vandaag ophalen? Alleen als er een wedstrijd bezig is, binnen 15 minuten begint of net klaar zou moeten zijn."""
    for m in today_file["wedstrijden"]:
        if m["status"] in LIVE:
            return True
        kickoff = datetime.fromisoformat(m["aftrap"])
        if m["status"] not in DONE and kickoff - timedelta(minutes=15) <= now <= kickoff + timedelta(hours=3):
            return True
    return False


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

    index, leagues, standings = previous_site(os.environ.get("PAGES_URL", "").rstrip("/"))
    utc_day = str(datetime.now(timezone.utc).date())
    api = Api(key, index.get("calls", 0) if index.get("calls_dag") == utc_day else 0, None)
    if mode != "herbouw":
        try:
            api.check_status()
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            print(f"status niet op te vragen ({e})")
    fetched_leagues = dict(index.get("competities", {}))
    live_fetched = index.get("live")

    if mode == "live" and not leagues:
        mode = "seizoen"  # eerste run: eerst het hele seizoen
    try:
        if mode in ("seizoen", "alles"):
            before = len(leagues)
            seasons = {}
            fetch_season(api, leagues, seasons)
            stamp = now.isoformat(timespec="seconds")
            fetched_leagues = {str(lid): stamp for lid in leagues}
            print(f"seizoen: {len(leagues)} competities ({len(leagues) - before} nieuw)")
            fetch_standings(api, leagues, seasons, standings, now)
        elif mode == "live":
            days = [today]
            if now.hour < 2:
                days.insert(0, today - timedelta(days=1))  # net na middernacht: gisteren afronden
            for d in days:
                if d == today and not needs_live_refresh(day_file(leagues, str(d)), now):
                    print("live: niets bezig, geen call")
                    continue
                raw = api.get("fixtures", date=str(d), timezone="Europe/Amsterdam")
                ours = {"response": [f for f in raw.get("response", []) if f["league"]["id"] in LEAGUES]}
                n = apply_live(leagues, compact(ours, str(d)))
                live_fetched = now.isoformat(timespec="seconds")
                print(f"live {d}: {len(ours['response'])} wedstrijden, {n} veranderd")
    except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
        print(f"gestopt: {e}")

    if not leagues:
        # Niets opgehaald en ook niets van de vorige site: de website niet leeg overschrijven
        print("Geen seizoensgegevens; website blijft zoals hij is.")
        if gh_output := os.environ.get("GITHUB_OUTPUT"):
            with open(gh_output, "a") as f:
                f.write("changed=false\n")
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
    content = hashlib.sha1("".join(texts).encode()).hexdigest()

    new_index = {
        "formaat": FORMAAT,
        "bijgewerkt": now.isoformat(timespec="seconds"),
        "dagen": {d: (live_fetched if d == str(today) and live_fetched else min(fetched_leagues.values(), default=""))
                  for d in window},
        "competities": fetched_leagues,
        "standen": {str(lid): file.get("bijgewerkt") for lid, file in sorted(standings.items())},
        "live": live_fetched,
        "calls_dag": utc_day,
        "calls": api.calls,
        "over": api.remaining,
        "inhoud": content,
        "bron": "API-Football (api-sports.io)",
    }
    (SITE / "index.json").write_text(json.dumps(new_index, indent=1), encoding="utf-8")

    changed = content != index.get("inhoud") or mode in ("alles", "herbouw")
    print(f"Modus {mode}: {api.calls} calls vandaag, nog {api.remaining} over, "
          f"{'nieuwe inhoud' if changed else 'niets veranderd'}")
    if gh_output := os.environ.get("GITHUB_OUTPUT"):
        with open(gh_output, "a") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")


if __name__ == "__main__":
    main()

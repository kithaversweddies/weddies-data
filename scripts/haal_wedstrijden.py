#!/usr/bin/env python3
"""
Weddies – wedstrijden ophalen voor de app (draait als GitHub Action).

Haalt bij API-Football de wedstrijden van gisteren, vandaag en morgen op, houdt alleen de competities
uit competities.json over en schrijft ze naar site/ (dat wordt via GitHub Pages online gezet):

  site/index.json                    wanneer wat is bijgewerkt + API-tellerstand
  site/wedstrijden/<yyyy-mm-dd>.json alleen wat de app nodig heeft, in Weddies' eigen compacte formaat
                                     (zie compact() en README.md)
  site/seizoen.json                  clubs per competitie in het huidige seizoen (voor het paspoort)

De ruwe API-Football-data gaat niet online; alleen het compacte formaat.

Zuinig met de 100 calls per dag:
- 'live'-runs (elke 10 min overdag) halen alleen vandaag op, en alleen als er een wedstrijd bezig is
  of binnen 15 minuten begint (of als 'vandaag' ouder is dan 3 uur);
- 'dag'-runs ('s nachts/'s ochtends) halen gisteren (tot hij definitief is), vandaag en morgen op;
- harde grens: nooit meer dan MAX_CALLS per dag, en stoppen als de API minder dan MIN_REMAINING over meldt.

Omdat GitHub Pages bij elke run helemaal opnieuw wordt neergezet, leest het script eerst de vorige versie
van de website in en neemt het de bestanden over die het niet opnieuw ophaalt.

Omgevingsvariabelen: API_FOOTBALL_KEY (GitHub Secret), PAGES_URL, MODE (live/dag/alles/herbouw), SITE_DIR.
'herbouw' haalt niets op bij de API (0 calls) en zet alleen de bestanden opnieuw neer.
"""

import html
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

API = "https://v3.football.api-sports.io/fixtures"
TZ = ZoneInfo("Europe/Amsterdam")
MAX_CALLS = 90        # van de 100 per dag; de rest blijft over voor stadions.py en handwerk
MIN_REMAINING = 10    # stoppen als API-Football minder dan dit over meldt
KEEP_DAYS = 7         # zoveel dagen terug blijven online staan
LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
DONE = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
FORMAAT = 1           # versie van het compacte formaat; ophogen bij een verandering die oude apps breekt

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(os.environ.get("SITE_DIR", ROOT / "site"))
LEAGUES = set(json.loads((ROOT / "competities.json").read_text())["competities"])


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "weddies-data", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")), resp.headers


def previous_site(pages_url):
    """De vorige versie van de website: index, dagen en seizoen (leeg bij de allereerste run)."""
    if not pages_url:
        return {}, {}, {}
    stamp = int(datetime.now().timestamp())

    def fetch(path):
        try:
            return get_json(f"{pages_url}/{path}?t={stamp}")[0]
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            return None

    index = fetch("index.json")
    if index is None:
        return {}, {}, {}
    days = {day: data for day in index.get("dagen", {}) if (data := fetch(f"wedstrijden/{day}.json")) is not None}
    return index, days, fetch("seizoen.json") or {}


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


def update_season(season, day_files):
    """Clubs per competitie bijhouden uit de wedstrijden (het gratis plan geeft geen clublijsten van dit
    seizoen). Een lijst heet pas 'compleet' als er in minstens twee speelronden alle bekende clubs
    (op hooguit één na, vanwege een vrije ronde) speelden."""
    comps = season.setdefault("competities", {})
    for data in day_files:
        for m in data.get("wedstrijden", []):
            lg = data["competities"][str(m["competitie"])]
            key, year = str(m["competitie"]), lg.get("seizoen") or 0
            comp = comps.get(key)
            if comp is None or year > comp["seizoen"]:
                comp = comps[key] = {"naam": lg["naam"], "land": lg["land"], "seizoen": year,
                                     "clubs": {}, "rondes": {}}
            elif year < comp["seizoen"]:
                continue  # wedstrijd uit een vorig seizoen
            home, away = m["thuis"], m["uit"]
            comp["clubs"][str(home["id"])] = home["naam"]
            comp["clubs"][str(away["id"])] = away["naam"]
            rnd = m.get("ronde") or "?"
            comp["rondes"][rnd] = sorted(set(comp["rondes"].get(rnd, [])) | {home["id"], away["id"]})
            weekday = datetime.fromisoformat(m["aftrap"]).weekday()
            pairs = comp.setdefault("paren", {})
            pairs[str(m["id"])] = [home["id"], away["id"], weekday]
    for comp in comps.values():
        comp["groepen"] = split_groups(comp)
    return season


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


def fetch_day(day, key):
    """Eén dag bij API-Football ophalen, direct omgezet naar het compacte formaat."""
    data, headers = get_json(f"{API}?date={day}&timezone=Europe/Amsterdam", {"x-apisports-key": key})
    if data.get("errors"):
        raise RuntimeError(f"API-Football: {data['errors']}")
    ours = {"response": [f for f in data.get("response", []) if f["league"]["id"] in LEAGUES]}
    remaining = headers.get("x-ratelimit-requests-remaining")
    return compact(ours, day), int(remaining) if remaining and remaining.isdigit() else None


def needs_live_refresh(day_data, fetched_at, now):
    """Vandaag opnieuw ophalen? Alleen als er iets gebeurt of de gegevens oud zijn."""
    if day_data is None or fetched_at is None:
        return True
    if now - fetched_at > timedelta(hours=3):
        return True
    for m in day_data.get("wedstrijden", []):
        status = m["status"]
        if status in LIVE:
            return True
        kickoff = datetime.fromisoformat(m["aftrap"])
        if status not in DONE and kickoff - timedelta(minutes=15) <= now <= kickoff + timedelta(hours=3):
            return True
    return False


def is_final(day_data, fetched_at, day):
    """Gisteren is definitief als hij ná afloop van die dag is opgehaald en alles afgelopen is."""
    if day_data is None or fetched_at is None:
        return False
    day_end = datetime.fromisoformat(day).replace(tzinfo=TZ) + timedelta(days=1, hours=1)
    return fetched_at >= day_end and all(m["status"] in DONE for m in day_data["wedstrijden"])


def main():
    key = os.environ.get("API_FOOTBALL_KEY", "")
    mode = os.environ.get("MODE", "alles")
    if not key and mode != "herbouw":
        sys.exit("API_FOOTBALL_KEY ontbreekt (GitHub Secret).")
    now = datetime.now(TZ)
    today = now.date()
    dates = {"gisteren": str(today - timedelta(days=1)), "vandaag": str(today), "morgen": str(today + timedelta(days=1))}

    index, days, season = previous_site(os.environ.get("PAGES_URL", "").rstrip("/"))
    had_season = bool(season.get("competities"))
    fetched = {d: datetime.fromisoformat(t) for d, t in index.get("dagen", {}).items()}

    # API-Football telt per dag in UTC
    utc_day = str(datetime.now(timezone.utc).date())
    calls = index.get("calls", 0) if index.get("calls_dag") == utc_day else 0
    remaining = index.get("over") if index.get("calls_dag") == utc_day else None

    wanted = []
    if mode == "live":
        if needs_live_refresh(days.get(dates["vandaag"]), fetched.get(dates["vandaag"]), now):
            wanted = [dates["vandaag"]]
    elif mode == "dag":
        if not is_final(days.get(dates["gisteren"]), fetched.get(dates["gisteren"]), dates["gisteren"]):
            wanted.append(dates["gisteren"])
        wanted += [dates["vandaag"], dates["morgen"]]
    elif mode == "herbouw":
        wanted = []
    else:
        wanted = list(dates.values())

    done = []
    for day in wanted:
        if calls >= MAX_CALLS or (remaining is not None and remaining < MIN_REMAINING):
            print(f"Grens bereikt ({calls} calls vandaag, nog {remaining} over): {day} overgeslagen")
            break
        try:
            days[day], left = fetch_day(day, key)
        except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
            print(f"{day}: niet gelukt ({e})")
            continue
        calls += 1
        remaining = left if left is not None else remaining
        fetched[day] = now
        done.append(day)
        print(f"{day}: {len(days[day]['wedstrijden'])} wedstrijden")

    # Alleen de laatste KEEP_DAYS dagen (en morgen) online houden
    oldest = str(today - timedelta(days=KEEP_DAYS))
    keep = sorted(d for d in days if oldest <= d <= dates["morgen"] and d in fetched)

    (SITE / "wedstrijden").mkdir(parents=True, exist_ok=True)
    for day in keep:
        (SITE / "wedstrijden" / f"{day}.json").write_text(
            json.dumps(days[day], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    new_index = {
        "formaat": FORMAAT,
        "bijgewerkt": now.isoformat(timespec="seconds"),
        "dagen": {d: fetched[d].isoformat(timespec="seconds") for d in keep},
        "calls_dag": utc_day,
        "calls": calls,
        "over": remaining,
        "bron": "API-Football (api-sports.io)",
    }
    (SITE / "index.json").write_text(json.dumps(new_index, indent=1), encoding="utf-8")

    # Seizoen: vorige stand + startset uit de repo (seed/) + alle dagen die nu online staan
    seeds = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "seed").glob("dag-*.json"))]
    season = update_season(season, seeds + [days[d] for d in keep])
    season["bijgewerkt"] = now.isoformat(timespec="seconds")
    (SITE / "seizoen.json").write_text(json.dumps(season, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"Modus {mode}: {len(done)} dag(en) opgehaald, {calls} calls vandaag, nog {remaining} over")
    if gh_output := os.environ.get("GITHUB_OUTPUT"):
        with open(gh_output, "a") as f:
            f.write(f"changed={'true' if done or not index or not had_season or mode == 'herbouw' else 'false'}\n")


if __name__ == "__main__":
    main()

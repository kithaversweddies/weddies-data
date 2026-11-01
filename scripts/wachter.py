#!/usr/bin/env python3
"""
Weddies – de wachter: kijkt of de wedstrijddata bij Supabase vers is (0 calls bij API-Football).

De Edge Function "wedstrijden" bij Supabase zet na elke run index.json neer (zie wd_index in
appstore/Supabase - wedstrijddata.sql in de app-repo). Deze Action leest die elke 30 minuten en wordt rood als:
- index.json niet te lezen is (3 pogingen);
- er een wedstrijd bezig is en de live standen ouder zijn dan 10 minuten;
- het seizoen ouder is dan 9 uur (het wordt elke 4 uur opgehaald);
- een taak 3 keer achter elkaar mislukte.
Bij een rode run stuurt GitHub vanzelf een mail. Een eigen maildienst of extra sleutel is dus niet nodig.

Omgevingsvariabele: SUPABASE_DATA_URL (de openbare bucket, zonder / aan het eind).
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

URL = os.environ.get("SUPABASE_DATA_URL",
                     "https://fltzibppgreiyuvtssro.supabase.co/storage/v1/object/public/data").rstrip("/")
LIVE_MAX_MIN = 10
SEASON_MAX_H = 9
MAX_FAILURES = 3


def read_index():
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(f"{URL}/index.json?t={int(time.time())}", timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001 – elke fout telt als "niet te lezen"
            last = e
            time.sleep(20)
    raise SystemExit(f"::error::index.json bij Supabase niet te lezen: {last!r}")


def age_minutes(stamp, now):
    if not stamp:
        return None
    return (now - datetime.fromisoformat(stamp)).total_seconds() / 60


def main():
    index = read_index()
    now = datetime.now(timezone.utc)
    problems = []
    live_age = age_minutes(index.get("live"), now)
    if index.get("live_nodig") and (live_age is None or live_age > LIVE_MAX_MIN):
        problems.append(f"live standen {live_age:.0f} min oud terwijl er iets bezig is" if live_age is not None
                        else "live standen nog nooit gelukt terwijl er iets bezig is")
    season_age = age_minutes(index.get("seizoen"), now)
    if season_age is None or season_age > SEASON_MAX_H * 60:
        problems.append(f"seizoen {season_age / 60:.1f} uur oud" if season_age is not None else "seizoen ontbreekt")
    for task, info in (index.get("taken") or {}).items():
        if (info.get("fouten_op_rij") or 0) >= MAX_FAILURES:
            problems.append(f"{task}: {info['fouten_op_rij']} keer achter elkaar mislukt ({info.get('laatste_fout')})")
    over = index.get("over")
    print(f"index.json van {index.get('bijgewerkt')}: live {index.get('live')} (nodig: {index.get('live_nodig')}), "
          f"seizoen {index.get('seizoen')}, {index.get('calls_vandaag')} calls vandaag, nog {over} over")
    if over is not None and over < 1500:
        print(f"::warning::API-Football heeft nog maar {over} calls over vandaag")
    for p in problems:
        print(f"::error::{p}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()

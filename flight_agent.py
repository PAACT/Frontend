#!/usr/bin/env python3
"""
Flight price watcher: Hyderabad (HYD) -> Dallas/Fort Worth (DFW), round trip.

Checks Google Flights prices (via SerpApi) for every date pair in config.json,
records the history, and alerts you when the cheapest total price drops
below your threshold.

Usage:
  python3 flight_agent.py                 # check once (good for cron/launchd)
  python3 flight_agent.py --loop          # keep running, check every N hours
  python3 flight_agent.py --demo          # fake prices, to test alerts
  python3 flight_agent.py --threshold 1800  # override threshold for this run
  python3 flight_agent.py --history       # cheapest price seen per date
  python3 flight_agent.py --dates         # list the date options watched
  python3 flight_agent.py --all           # search every date now

Standard library only - no pip install needed.
"""

import argparse
import json
import os
import random
import smtplib
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "price_history.json"
SERPAPI_URL = "https://serpapi.com/search.json"


# ---------------------------------------------------------------- config

def load_config(path):
    if not path.exists():
        sys.exit(f"Missing {path.name}. Copy config.example.json to config.json and edit it.")
    cfg = json.loads(path.read_text())
    cfg.setdefault("origin", "HYD")
    cfg.setdefault("destination", "DFW")
    cfg.setdefault("adults", 2)
    cfg.setdefault("currency", "USD")
    cfg.setdefault("check_every_hours", 6)
    cfg.setdefault("flex_days", 0)
    cfg.setdefault("exact_stops", 1)          # only itineraries with exactly this many layovers
    cfg.setdefault("layover_airports", [])    # e.g. ["DXB"]; empty = any layover city
    cfg.setdefault("realert_drop", 25)
    cfg.setdefault("searches_per_run", 0)  # 0 = check every date pair each run
    if "threshold" not in cfg or not ("trips" in cfg or "departure_window" in cfg):
        sys.exit("config.json needs 'threshold' and either 'departure_window' or 'trips'.")
    return cfg


def add_months(d, months):
    """Add calendar months, clamping the day (e.g. Jan 31 + 1 month = Feb 28)."""
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue


def date_pairs(cfg):
    """All (outbound, return) pairs to watch, in date order."""
    return list(_date_pairs(cfg))


def _date_pairs(cfg):
    today = date.today()
    seen = set()

    # Mode 1: leave any day in a window, return a fixed trip length later.
    win = cfg.get("departure_window")
    if win:
        length = cfg.get("trip_length", {})
        out = date.fromisoformat(win["from"])
        last = date.fromisoformat(win["to"])
        step = int(win.get("every_n_days", 1))
        if "min_days" in length:
            lo = int(length["min_days"])
            hi = int(length.get("max_days", lo))
            stays = list(range(lo, hi + 1, int(length.get("step_days", 1))))
            if stays[-1] != hi:
                stays.append(hi)
        while out <= last:
            if "min_days" in length:
                returns = [out + timedelta(n) for n in stays]
            else:
                returns = [add_months(out, int(length.get("months", 0))) + timedelta(int(length.get("days", 0)))]
            for ret in returns:
                if out > today and (out, ret) not in seen:
                    seen.add((out, ret))
                    yield out, ret
            out += timedelta(step)

    # Mode 2: specific trips, +/- flex_days around each date.
    flex = int(cfg["flex_days"])
    for trip in cfg.get("trips", []):
        out0 = date.fromisoformat(trip["depart"])
        ret0 = date.fromisoformat(trip["return"])
        for d_out in range(-flex, flex + 1):
            for d_ret in range(-flex, flex + 1):
                out, ret = out0 + timedelta(d_out), ret0 + timedelta(d_ret)
                if out <= today or ret <= out or (out, ret) in seen:
                    continue
                seen.add((out, ret))
                yield out, ret


# ---------------------------------------------------------------- price lookup

def search_serpapi(cfg, out, ret):
    """Return a list of offers: {price, airlines, stops, duration_min, summary}."""
    key = os.environ.get("SERPAPI_KEY") or cfg.get("serpapi_key")
    if not key:
        sys.exit("No SerpApi key. Set SERPAPI_KEY or 'serpapi_key' in config.json (free at serpapi.com).")
    params = {
        "engine": "google_flights",
        "departure_id": cfg["origin"],
        "arrival_id": cfg["destination"],
        "outbound_date": out.isoformat(),
        "return_date": ret.isoformat(),
        "type": "1",                     # round trip
        "adults": str(cfg["adults"]),
        "travel_class": "1",             # economy
        "currency": cfg["currency"],
        "hl": "en",
        "gl": "us",
        "api_key": key,
    }
    if cfg["exact_stops"] is not None:
        # Ask Google for "N stops or fewer" (SerpApi: 1 nonstop, 2 one stop or
        # fewer, 3 two stops or fewer); filter_offers() then keeps exactly N.
        params["stops"] = str(min(int(cfg["exact_stops"]) + 1, 3))
    url = SERPAPI_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.load(resp)
    if "error" in data:
        raise RuntimeError(data["error"])

    offers = []
    for item in data.get("best_flights", []) + data.get("other_flights", []):
        if "price" not in item:
            continue
        legs = item.get("flights", [])
        airlines = sorted({leg.get("airline", "?") for leg in legs})
        route = " → ".join([legs[0]["departure_airport"]["id"]] +
                           [leg["arrival_airport"]["id"] for leg in legs]) if legs else ""
        offers.append({
            "price": float(item["price"]),
            "airlines": ", ".join(airlines),
            "stops": max(len(legs) - 1, 0),
            "layovers": [leg["arrival_airport"]["id"] for leg in legs[:-1]],
            "duration_min": item.get("total_duration"),
            "summary": route,
        })
    return offers, data.get("search_metadata", {}).get("google_flights_url")


def search_demo(cfg, out, ret):
    """Fake prices so you can test the alert path without an API key."""
    base = cfg["threshold"] * random.uniform(0.85, 1.25)
    options = [("Emirates", ["DXB"]), ("Qatar Airways", ["DOH"]), ("Etihad", ["AUH"]),
               ("Lufthansa, United", ["FRA", "ORD"]), ("Air India, American", ["DEL", "JFK"])]
    offers = []
    for _ in range(5):
        airline, stops = random.choice(options)
        offers.append({
            "price": round(base + random.uniform(-300, 400), 2),
            "airlines": airline,
            "stops": len(stops),
            "layovers": stops,
            "duration_min": random.randint(1300, 1900),
            "summary": " → ".join(["HYD", *stops, "DFW"]),
        })
    return offers, "https://www.google.com/travel/flights"


def filter_offers(cfg, offers):
    """Keep only itineraries with the required number of layovers (and, if set,
    only through the allowed layover airports)."""
    want = cfg["exact_stops"]
    allowed = {a.upper() for a in cfg["layover_airports"]}
    keep = []
    for o in offers:
        if want is not None and o["stops"] != int(want):
            continue
        if allowed and not set(o.get("layovers", [])) <= allowed:
            continue
        keep.append(o)
    return keep


# ---------------------------------------------------------------- alerts

def notify(cfg, title, body, link=None):
    alerts = cfg.get("alerts", {})
    print(f"\n🔔 {title}\n{body}\n{link or ''}\n")

    if alerts.get("macos", True) and sys.platform == "darwin":
        safe = lambda s: s.replace('"', "'")
        subprocess.run(["osascript", "-e",
                        f'display notification "{safe(body)}" with title "{safe(title)}" sound name "Glass"'],
                       check=False)

    topic = alerts.get("ntfy_topic")
    if topic:  # free phone push: install the ntfy app and subscribe to this topic
        try:
            req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body.encode(),
                                         headers={"Title": title, "Priority": "high",
                                                  **({"Click": link} if link else {})})
            urllib.request.urlopen(req, timeout=20)
        except Exception as e:
            print(f"ntfy failed: {e}")

    email = alerts.get("email")
    if email and email.get("to"):
        try:
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = title, email["from"], email["to"]
            msg.set_content(body + (f"\n\nBook / view: {link}" if link else ""))
            with smtplib.SMTP_SSL(email.get("smtp_host", "smtp.gmail.com"),
                                  int(email.get("smtp_port", 465))) as s:
                s.login(email["from"], os.environ.get("SMTP_PASSWORD") or email.get("password", ""))
                s.send_message(msg)
        except Exception as e:
            print(f"email failed: {e}")


# ---------------------------------------------------------------- state

def load_state():
    state = {}
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text())
    state.setdefault("checks", [])
    state.setdefault("cursor", 0)    # where the date rotation left off
    state.setdefault("alerted", {})  # "depart|return" -> price we last alerted at
    state.pop("last_alert_price", None)
    return state


def save_state(state):
    state["checks"] = state["checks"][-5000:]
    STATE_PATH.write_text(json.dumps(state, indent=2))


def fmt_money(cfg, x):
    return f"{cfg['currency']} {x:,.0f}"


def fmt_hours(minutes):
    return f"{minutes // 60}h{minutes % 60:02d}m" if minutes else ""


def pick_batch(cfg, state, check_all=False):
    """Which date pairs to search this run. Rotates through the list so a
    small API budget still covers every date over a few runs."""
    pairs = date_pairs(cfg)
    n = int(cfg["searches_per_run"])
    if check_all or n <= 0 or n >= len(pairs):
        return pairs, len(pairs)
    start = state["cursor"] % len(pairs)
    batch = [pairs[(start + i) % len(pairs)] for i in range(n)]
    state["cursor"] = (start + n) % len(pairs)
    return batch, len(pairs)


# ---------------------------------------------------------------- one check

def run_check(cfg, demo=False, check_all=False):
    search = search_demo if demo else search_serpapi
    state = load_state()
    threshold = float(cfg["threshold"])
    drop = float(cfg["realert_drop"])
    now = datetime.now().isoformat(timespec="seconds")

    batch, total = pick_batch(cfg, state, check_all)
    if not batch:
        print("No future dates left to check — update the dates in config.json.")
        return
    print(f"Searching {len(batch)} of {total} date options "
          f"(threshold {fmt_money(cfg, threshold)} for {cfg['adults']} adults):")

    deals = []
    for out, ret in batch:
        key = f"{out}|{ret}"
        try:
            offers, link = search(cfg, out, ret)
        except Exception as e:
            print(f"  {out} ↔ {ret}: lookup failed ({e})")
            continue
        offers = filter_offers(cfg, offers)
        if not offers:
            print(f"  {out} ↔ {ret}: no flights matching your layover rules")
            continue
        cheapest = min(offers, key=lambda o: o["price"])
        price = cheapest["price"]
        record = {"time": now, "depart": out.isoformat(), "return": ret.isoformat(),
                  "link": link, **cheapest}
        state["checks"].append(record)

        flag = ""
        last = state["alerted"].get(key)
        if price < threshold:
            # Alert the first time a date dips under, then only on further real drops.
            if last is None or price <= last - drop:
                deals.append(record)
                state["alerted"][key] = price
                flag = "  ⬅ DEAL"
            else:
                flag = "  (below, already alerted)"
        elif last is not None:
            del state["alerted"][key]  # back above threshold; next dip alerts again
        print(f"  {out} ↔ {ret}: {fmt_money(cfg, price):>11}  "
              f"{cheapest['summary']}  {cheapest['airlines']}{flag}")
        if not demo:
            time.sleep(1)  # be gentle with the API

    if deals:
        deals.sort(key=lambda r: r["price"])
        top = deals[0]
        lines = [f"{d['depart']} → {d['return']}: {fmt_money(cfg, d['price'])} "
                 f"via {'/'.join(d.get('layovers', [])) or 'nonstop'} ({d['airlines']}, {fmt_hours(d.get('duration_min'))})".replace(", )", ")")
                 for d in deals[:5]]
        more = f"\n+{len(deals) - 5} more" if len(deals) > 5 else ""
        notify(cfg,
               f"✈️ {cfg['origin']}→{cfg['destination']} from {fmt_money(cfg, top['price'])} for {cfg['adults']}",
               f"Below your {fmt_money(cfg, threshold)} limit:\n" + "\n".join(lines) + more,
               top.get("link"))
    else:
        print("No new deals below threshold this run.")

    save_state(state)


def show_history(cfg):
    state = load_state()
    if not state["checks"]:
        print("No history yet.")
        return
    # Latest price seen for each date pair, cheapest first.
    latest = {}
    for r in state["checks"]:
        latest[(r["depart"], r["return"])] = r
    print(f"Latest price per date option (threshold {fmt_money(cfg, float(cfg['threshold']))}):")
    for r in sorted(latest.values(), key=lambda r: r["price"])[:20]:
        print(f"  {r['depart']} ↔ {r['return']}  {fmt_money(cfg, r['price']):>11}  "
              f"{r['airlines']}  (checked {r['time'][:16].replace('T', ' ')})")
    print(f"\n{len(latest)} of {len(date_pairs(cfg))} date options checked so far.")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="HYD ⇄ DFW flight price watcher")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--loop", action="store_true", help="keep running and check on an interval")
    ap.add_argument("--demo", action="store_true", help="use fake prices (no API key needed)")
    ap.add_argument("--threshold", type=float, help="override the alert threshold")
    ap.add_argument("--history", action="store_true", help="print recent prices and exit")
    ap.add_argument("--all", action="store_true", help="search every date option now (ignores searches_per_run)")
    ap.add_argument("--dates", action="store_true", help="list the date options being watched and exit")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    if args.demo:  # keep fake prices out of the real history
        global STATE_PATH
        STATE_PATH = HERE / "price_history.demo.json"
    if args.threshold is not None:
        cfg["threshold"] = args.threshold
    if args.history:
        return show_history(cfg)
    if args.dates:
        pairs = date_pairs(cfg)
        for out, ret in pairs:
            print(f"{out:%a %b %d %Y}  →  {ret:%a %b %d %Y}")
        return print(f"\n{len(pairs)} date options.")

    while True:
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] Checking {cfg['origin']} ⇄ {cfg['destination']} "
              f"for {cfg['adults']} adults…")
        run_check(cfg, demo=args.demo, check_all=args.all)
        if not args.loop:
            break
        hours = float(cfg["check_every_hours"])
        print(f"Next check in {hours:g}h.\n")
        time.sleep(hours * 3600)


if __name__ == "__main__":
    main()

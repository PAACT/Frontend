# HYD ⇄ DFW flight price agent

Checks Google Flights (via SerpApi) for round-trip fares Hyderabad → Dallas/Fort Worth
for 2 adults, and alerts you when the cheapest **total** price drops below your threshold.

## Setup (2 minutes)
1. Get a free SerpApi key at https://serpapi.com (free plan = 250 searches/month).
2. Edit `config.json`:
   - `threshold` – alert when the total for both passengers is below this.
   - `departure_window` + `trip_length` – currently: leave any day **Feb 17 – Mar 30, 2027**,
     return **5 months 12 days** later (42 date options). Run `python3 flight_agent.py --dates` to list them.
     (Alternatively use `trips` with fixed dates and `flex_days`.)
   - `serpapi_key` – or `export SERPAPI_KEY=...` instead.
3. Test alerts with fake prices: `python3 flight_agent.py --demo`
4. Real check: `python3 flight_agent.py`

## Web UI
Open the dashboard in a browser with:

```bash
python3 ui_server.py
```

Then visit http://localhost:8000. The UI lets you:
- adjust the flight watch settings
- save config changes
- run a demo check or live price scan
- review the latest fare history and threshold metrics

## Alerts
- **Mac notification** – on by default.
- **Phone push** – install the free *ntfy* app, subscribe to a random topic name, put it in `ntfy_topic`.
- **Email** – fill `alerts.email`; for Gmail use an App Password via `export SMTP_PASSWORD=...`.

You get one alert when the price first dips under the threshold, then again only if it drops
another `realert_drop` (default 25) — so you're not spammed every run.

## Run it automatically
- Simple: `python3 flight_agent.py --loop` (checks every `check_every_hours`).
- Cron (every 6h): `0 */6 * * * cd /path/to/folder && /usr/bin/python3 flight_agent.py >> agent.log 2>&1`

Budget: each run searches `searches_per_run` dates (default 4), rotating through all 42.
With checks every 12h that's 8 searches/day ≈ 240/month — fits SerpApi's free 250 —
and every date gets re-checked about every 5 days. On a paid plan, raise `searches_per_run`
(or use `--all` to search every date right now, 42 searches).

Alerts are per date: each date alerts once when it dips under the threshold, and again only if it
drops another `realert_drop`. One notification lists all new deals from that run.

`python3 flight_agent.py --history` shows the latest price per date, cheapest first.

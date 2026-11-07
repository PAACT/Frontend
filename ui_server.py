#!/usr/bin/env python3
"""Small web UI for the flight price watcher.

Usage:
    python3 ui_server.py
Then open http://localhost:8000 in a browser.
"""

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "price_history.json"


def load_json(path: Path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def write_json(path: Path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n")


def summarize_history():
    state = load_json(STATE_PATH)
    checks = state.get("checks", [])
    latest = {}
    for record in checks:
        key = (record.get("depart"), record.get("return"))
        latest[key] = record
    return {
        "checks": sorted(latest.values(), key=lambda item: float(item.get("price", 0))),
        "latest_count": len(latest),
    }


class FlightUIHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            return self.handle_config_get()
        if parsed.path == "/api/history":
            return self.handle_history_get()
        if parsed.path == "/":
            self.path = "/index.html"
            return super().do_GET()
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            return self.handle_config_post()
        if parsed.path == "/api/run":
            return self.handle_run_post(parsed.query)
        self.send_error(404, "Not found")

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_config_get(self):
        config = load_json(CONFIG_PATH)
        self.send_json({"config": config})

    def handle_history_get(self):
        history = summarize_history()
        self.send_json(history)

    def handle_config_post(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length).decode("utf-8")
        payload = json.loads(raw or "{}")
        config = load_json(CONFIG_PATH)
        config.update(payload)
        write_json(CONFIG_PATH, config)
        self.send_json({"ok": True, "config": config})

    def handle_run_post(self, query):
        params = parse_qs(query)
        demo = params.get("demo", ["false"])[0].lower() in {"1", "true", "yes"}
        all_dates = params.get("all", ["false"])[0].lower() in {"1", "true", "yes"}

        cmd = [sys.executable, str(ROOT / "flight_agent.py")]
        if demo:
            cmd.append("--demo")
        if all_dates:
            cmd.append("--all")

        import subprocess

        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=180)
        payload = {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
        self.send_json(payload)


def main():
    port = 8000
    server = ThreadingHTTPServer(("0.0.0.0", port), FlightUIHandler)
    print(f"Flight UI running at http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down flight UI.")
        server.server_close()


if __name__ == "__main__":
    main()

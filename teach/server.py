#!/usr/bin/env python3
"""Web server of the "Correct Qwen" game. Standard library only.

    python3 teach/server.py                    # http://127.0.0.1:8000 on the board
    python3 teach/server.py --host 0.0.0.0     # open it to the network

Open it from the laptop with `ssh -L 8000:127.0.0.1:8000 arduino@<board-ip>` or
`adb forward tcp:8000 tcp:8000`, then http://127.0.0.1:8000.
No model is called while a student plays: Qwen's attempts, the types and the
similar problems were all prepared ahead of time by prepare_app.py.

GET /api/led returns the last event (right, wrong, hint, step...) for the App
Lab app that draws it on the LED matrix.
"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from teach import game  # noqa: E402
from teach.kinds import TIERS, TYPES  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


class App:
    def __init__(self, db_path):
        self.db = game.connect(db_path)
        self.lock = threading.Lock()  # one SQLite connection, shared by the threads
        self.led = {"seq": 0, "event": "idle", "time": time.time()}

    def set_led(self, event, **extra):
        self.led = {"seq": self.led["seq"] + 1, "event": event, "time": time.time(), **extra}

    def meta(self):
        return {"types": [{k: t[k] for k in ("key", "name", "icon", "color")} for t in TYPES],
                "tiers": [{k: t[k] for k in ("tier", "name", "medal")} for t in TIERS],
                "available": game.available(self.db), "to_clear": game.SOLVED_TO_CLEAR,
                "points": game.POINTS}

    def call(self, path, body, query):
        db = self.db
        if path == "/api/meta":
            return self.meta()
        if path == "/api/led":
            return self.led
        if path == "/api/login":
            uid = game.user_id(db, body.get("name", ""))
            return {"uid": uid, "stats": game.stats(db, uid)}
        uid = int(body.get("uid") or query.get("uid", ["0"])[0])
        if path == "/api/stats":
            return game.stats(db, uid)
        if path == "/api/history":
            return game.history(db, uid)
        if path == "/api/report":
            return game.help_report(db)
        if path == "/api/next":
            kind, mode = body.get("type"), body.get("mode", "solve")
            pid = game.pick(db, uid, kind, mode=mode)
            if not pid:
                return {"done": True}
            play_id = game.start_play(db, uid, pid, mode)
            problem = game.problem_public(db, pid, mode)
            self.set_led("problem", tier=problem["tier"], type=problem["type"])
            kinds = game.ladder(db, pid, mode)
            return {"play_id": play_id, "mode": mode, "problem": problem,
                    "help_total": len(kinds), "help_kinds": kinds}
        play_id = int(body.get("play_id", 0))
        if path == "/api/mark":
            step = int(body.get("step", 0))
            self.set_led("step", step=step)
            return {"ok": True}
        if path == "/api/help":
            out = game.help_rung(db, play_id, uid)
            self.set_led("hint", n=out["n"])
            return out
        if path == "/api/sibling":
            return game.check_sibling(db, play_id, uid, str(body.get("answer", "")))
        if path == "/api/answer":
            out = game.answer(db, play_id, uid, body.get("step"), str(body.get("answer", "")),
                              str(body.get("fix", "")))
            if out["right"]:
                out["stats"] = game.stats(db, uid)
            self.set_led("right" if out["right"] else "wrong")
            return out
        if path == "/api/giveup":
            out = game.give_up(db, play_id, uid)
            out["stats"] = game.stats(db, uid)
            self.set_led("giveup")
            return out
        return None


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, code, body, kind="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def handle_api(self, body):
            url = urlparse(self.path)
            try:
                with app.lock:
                    out = app.call(url.path, body, parse_qs(url.query))
            except (ValueError, KeyError, TypeError) as error:
                return self.send(400, {"error": str(error)})
            if out is None:
                return self.send(404, {"error": "not found"})
            return self.send(200, out)

        def do_GET(self):
            if self.path.startswith("/api/"):
                return self.handle_api({})
            if self.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "static", "index.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html; charset=utf-8")
            return self.send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self.send(400, {"error": "bad json"})
            return self.handle_api(body)

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="data/app.db")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    app = App(args.db)
    n = app.db.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
    print(f"{n} problems in {args.db}")
    print(f"Open http://{args.host}:{args.port} (Ctrl+C to stop)", flush=True)
    ThreadingHTTPServer((args.host, args.port), make_handler(app)).serve_forever()


if __name__ == "__main__":
    main()

"""End-to-end check of the web API, the way a student uses it.

    python3 teach/make_demo_db.py
    python3 teach/server.py --db data/demo.db --port 8001 &
    python3 teach/test_api.py --url http://127.0.0.1:8001

Uses the made-up demo problems only. Every step prints OK or stops with the
reason, so it doubles as a pre-demo checklist.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def call(base, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(label, ok, detail=""):
    print(f"{'OK  ' if ok else 'FAIL'} {label}{(': ' + str(detail)) if detail and not ok else ''}")
    if not ok:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8001")
    base = ap.parse_args().url

    t0 = time.perf_counter()
    status, page = 200, urllib.request.urlopen(base + "/", timeout=10).read().decode()
    check("page loads, in English", '<html lang="en">' in page and "Pick a topic" in page)
    _, meta = call(base, "/api/meta")
    check("meta: 6 topics and 3 tiers in English", len(meta["types"]) == 6 and meta["tiers"][0]["name"] == "Easy",
          meta["tiers"])
    _, login = call(base, "/api/login", {"name": f"Tester {int(time.time())}"})
    uid = login["uid"]
    check("login: new student starts at zero", login["stats"]["solved"] == 0 and "streaks" in login["stats"])
    status, err = call(base, "/api/login", {"name": "   "})
    check("login: empty name is refused in English", status == 400 and "name" in err["error"])

    # Solve mode: reveal steps, a wrong answer, then the right one
    _, nxt = call(base, "/api/next", {"uid": uid, "type": "calculo", "mode": "solve"})
    play, prob = nxt["play_id"], nxt["problem"]
    check("solve: a problem without Qwen's steps or the answer", "steps" not in prob and "correct_value" not in prob)
    check("solve: help is only solution steps", set(nxt["help_kinds"]) == {"guide"}, nxt["help_kinds"])
    _, h1 = call(base, "/api/help", {"uid": uid, "play_id": play})
    check("help: first step revealed", h1["kind"] == "guide" and h1["step"] == 1 and h1["text"])
    status, bad = call(base, "/api/answer", {"uid": uid, "play_id": play, "answer": "hello"})
    check("answer: nonsense is refused, in English", status == 400 and bad["error"].isascii(), bad)
    _, wrong = call(base, "/api/answer", {"uid": uid, "play_id": play, "answer": "999"})
    check("answer: a wrong number is wrong", wrong["right"] is False)
    answers = {"demo-0": "4*3-4", "demo-1": "15"}
    _, right = call(base, "/api/answer", {"uid": uid, "play_id": play, "answer": answers[prob["id"]]})
    check("answer: a calculation is evaluated and accepted", right["right"] is True, right)
    check("points: lowered by the hint and the second try", 0 < right["points"] < meta["points"][str(prob["tier"])],
          right["points"])
    status, again = call(base, "/api/answer", {"uid": uid, "play_id": play, "answer": "8"})
    check("answer: a finished play can't be answered again", status == 400)

    # Correct mode: must mark a step first
    _, nxt = call(base, "/api/next", {"uid": uid, "type": "algebra", "mode": "correct"})
    play, prob = nxt["play_id"], nxt["problem"]
    check("correct: Qwen's steps and answer are shown", prob["steps"] and prob["qwen_answer"])
    status, err = call(base, "/api/answer", {"uid": uid, "play_id": play, "answer": "4"})
    check("correct: answering without marking a step is refused", status == 400 and "step" in err["error"])
    call(base, "/api/mark", {"uid": uid, "play_id": play, "step": 2})
    _, led = call(base, "/api/led")
    check("LED: the marked step is published", led["event"] == "step" and led["step"] == 2)
    _, gave = call(base, "/api/giveup", {"uid": uid, "play_id": play})
    check("give up: the answer is shown", gave["value"])

    # The queue: the failed problem's twin comes next
    _, nxt = call(base, "/api/next", {"uid": uid, "type": "algebra", "mode": "solve"})
    check("queue: after failing, its twin comes next", nxt["problem"]["id"] in ("demo-2", "demo-3"), nxt["problem"]["id"])

    # Dashboard data
    _, stats = call(base, "/api/stats", {"uid": uid})
    _, hist = call(base, "/api/history", {"uid": uid})
    hex_total = round(sum(t["points"] for t in stats["types"].values()), 2)
    check("history: last total equals the hexagon", hist["plays"] and hist["plays"][-1]["total"] == hex_total,
          (hist["plays"][-1]["total"], hex_total))
    check("streaks: present for both kinds", set(stats["streaks"]) == {"no_help", "first_try"})
    status, _ = call(base, "/api/report", {})
    check("report endpoint answers", status == 200)
    status, _ = call(base, "/api/nothing", {})
    check("unknown endpoint is a 404", status == 404)
    print(f"\nAll checks passed in {1000 * (time.perf_counter() - t0):.0f} ms")


if __name__ == "__main__":
    main()

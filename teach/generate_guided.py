#!/usr/bin/env python3
"""Write a verified step-by-step solution for each problem, ahead of time, with a
small math model (Qwen2.5-Math-1.5B-Instruct), and store it in SQLite.

    python3 teach/generate_guided.py --limit 15          # pilot
    python3 teach/generate_guided.py                     # every problem in data/teach.db

The model is told the correct answer, so it only has to explain the way there,
which is much easier than solving. Then Python checks the solution before
keeping it:
  - its final answer must be the correct option's value
  - every calculation written as "<numbers and + - x / ^> = <number>" must be true
Only solutions that pass become the help ladder: one step at a time, never the last.

The model runs as a second llama-server, started by hand (see teach/README.md):
    llama-server -m ~/models/Qwen2.5-Math-1.5B-Instruct-Q4_K_M.gguf --port 8084 ...
Run it again to resume. Standard library only.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from copilot.client import post  # noqa: E402
from copilot.config import CONFIG  # noqa: E402
from teach.game import evaluate  # noqa: E402

SYSTEM = (
    "You are a patient math teacher. You are given a competition problem and its correct answer. "
    "Write a clear solution that reaches exactly that answer, in 2 to 6 numbered steps. "
    "Each step does one thing: states a fact deduced from the problem, or does one calculation "
    "written as '<calculation> = <result>'. Use the numbers of the problem. Plain text, no LaTeX. "
    "Then give the final answer.")

# 2 to 6 steps "Step N: ...", then "Answer: <number>". No $ or backslash: no LaTeX.
GRAMMAR = r'''
root   ::= step step step? step? step? step? answer
step   ::= "Step " [1-6] ": " [^\n$\\]{1,300} "\n"
answer ::= "Answer: " "-"? [0-9]{1,7} ("." [0-9]{1,4})? ("/" [0-9]{1,4})?
'''

SCHEMA = """
CREATE TABLE IF NOT EXISTS guided (
    id TEXT PRIMARY KEY,
    steps TEXT,          -- JSON list of steps, as the model wrote them
    answer TEXT,         -- the number after "Answer:"
    ok INTEGER,          -- 1 passed every check
    reason TEXT,         -- why it failed
    checks INTEGER,      -- calculations found and checked
    failed INTEGER,      -- calculations that were false
    seconds REAL, prompt_n INTEGER, predicted_n INTEGER
)"""

# "<expression> = <number>": an expression made only of numbers, operators and parentheses
# (one flat character class: a nested quantifier here backtracks forever on long lines)
CALC = re.compile(r"([\d.,()+\-*/×x÷^ ]+)=\s*(-?\d+(?:[.,]\d+)?(?:/\d+)?)")


def check_calculations(steps):
    """Return (checked, failed): every 'a op b = c' in the steps, evaluated exactly."""
    checked = failed = 0
    for step in steps:
        for expr, result in CALC.findall(step):
            expr = expr.strip()
            if not re.search(r"\d\s*\)?\s*[+\-*/×x÷^]\s*\(?\s*\d", expr):
                continue  # "x = 5" or "= 12" alone: nothing to compute
            try:
                ok = evaluate(expr) == evaluate(result)
            except ValueError:
                continue
            checked += 1
            failed += not ok
    return checked, failed


def same(a, b):
    try:
        return evaluate(a) == evaluate(b)
    except ValueError:
        return a.strip() == b.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/teach.db")
    ap.add_argument("--port", type=int, default=8084)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=900)
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/v1/chat/completions"

    db = sqlite3.connect(args.db)
    db.execute(SCHEMA)
    rows = db.execute("SELECT a.id, a.text, a.correct_value FROM attempts a LEFT JOIN guided g ON g.id = a.id "
                      "WHERE g.id IS NULL AND a.status = 'ok' ORDER BY a.rowid").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows)} problems to solve with verification", flush=True)

    for i, (pid, text, correct) in enumerate(rows, 1):
        start = time.perf_counter()
        steps, answer, reason, timings, checked, failed = [], "", "", {}, 0, 0
        try:
            response = post(url, {
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": f"Problem: {text}\nCorrect answer: {correct}"}],
                "grammar": GRAMMAR, "max_tokens": 700, "temperature": 0,
                "seed": int(CONFIG["SEED"]), "cache_prompt": True,
            }, timeout=args.timeout)
            content = response["choices"][0]["message"]["content"]
            timings = response.get("timings", {})
            steps = [line.split(": ", 1)[1].strip() for line in content.splitlines()
                     if line.startswith("Step ") and ": " in line]
            match = re.search(r"Answer:\s*(\S+)", content)
            answer = match.group(1) if match else ""
            checked, failed = check_calculations(steps)
            if not same(answer, correct):
                reason = f"answer {answer} is not {correct}"
            elif failed:
                reason = f"{failed} of {checked} calculations are false"
        except Exception as error:  # noqa: BLE001
            reason = f"error: {error}"
        seconds = time.perf_counter() - start
        db.execute("INSERT OR REPLACE INTO guided VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (pid, json.dumps(steps), answer, int(not reason), reason, checked, failed, round(seconds, 1),
                    timings.get("prompt_n", 0), timings.get("predicted_n", 0)))
        db.commit()
        print(f"[{i}/{len(rows)}] {pid} {seconds:.0f} s, {checked} calcs checked: "
              f"{'VERIFIED' if not reason else 'REJECTED: ' + reason}", flush=True)
        for n, s in enumerate(steps, 1):
            print(f"    {n}. {s}", flush=True)
    n, ok = db.execute("SELECT COUNT(*), SUM(ok) FROM guided").fetchone()
    print(f"\nVerified {ok}/{n} solutions in {args.db}")
    db.close()


if __name__ == "__main__":
    main()

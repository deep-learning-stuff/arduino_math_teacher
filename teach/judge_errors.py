#!/usr/bin/env python3
"""Find the step where Qwen 0.8B goes wrong, with the math model as a judge.

    python3 teach/judge_errors.py --limit 20       # a first look
    python3 teach/judge_errors.py                  # every attempt in data/teach.db
    python3 teach/judge_errors.py --report         # sanity checks, no model calls

The judge (Qwen2.5-Math-1.5B, port 8084) reads the problem, the correct answer,
a verified reference solution when generate_guided.py has one, and Qwen's
numbered steps. A grammar makes it answer one digit: the first wrong step, or 0
if every step is right. Its probabilities give a confidence.

There are no hand labels, so --report checks the judge against what we know
for sure:
  - attempts where Qwen got the right answer: the judge should say 0
  - attempts with a provably false calculation: the judge should point at that
    step or an earlier one
  - the spread of the steps it picks: always "1" would mean it isn't reading
Standard library only.
"""
import argparse
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from copilot.client import post  # noqa: E402
from copilot.config import CONFIG  # noqa: E402
from teach.generate_guided import check_calculations  # noqa: E402

SYSTEM = (
    "You are a strict math teacher checking a student's solution. You get a competition problem, "
    "its correct answer, sometimes a correct reference solution, and the student's numbered steps. "
    "Find the FIRST step that contains a mistake: a wrong fact, a misread condition, a wrong idea "
    "or a wrong calculation. Reply with that step number only. Reply 0 if every step is correct.")

SCHEMA = """
CREATE TABLE IF NOT EXISTS judged (
    id TEXT PRIMARY KEY,
    step INTEGER,        -- first wrong step according to the judge, 0 = no mistake
    confidence REAL,     -- probability the judge gave to that digit
    probs TEXT,          -- JSON {digit: probability} for the top digits
    with_reference INTEGER,
    seconds REAL, prompt_n INTEGER
)"""


def ask(url, text, correct, steps, reference, timeout):
    n = len(steps)
    parts = [f"Problem: {text}", f"Correct answer: {correct}"]
    if reference:
        parts.append("Reference solution:\n" + "\n".join(f"- {s}" for s in reference))
    parts.append("Student's steps:\n" + "\n".join(f"Step {i}: {s}" for i, s in enumerate(steps, 1)))
    parts.append(f"First wrong step (1 to {n}), or 0 if there is none:")
    response = post(url, {
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}],
        "grammar": f"root ::= [0-{n}]", "max_tokens": 1, "temperature": 0,
        "seed": int(CONFIG["SEED"]), "cache_prompt": True,
        "logprobs": True, "top_logprobs": 7,
    }, timeout=timeout)
    choice = response["choices"][0]
    step = int(choice["message"]["content"].strip())
    probs = {}
    try:
        for item in choice["logprobs"]["content"][0]["top_logprobs"]:
            if item["token"].strip().isdigit():
                probs[item["token"].strip()] = round(math.exp(item["logprob"]), 3)
    except (KeyError, IndexError, TypeError):
        pass
    return step, probs.get(str(step)), probs, response.get("timings", {})


def report(db):
    rows = db.execute("""SELECT j.id, j.step, j.confidence, a.qwen_correct, a.steps FROM judged j
                         JOIN attempts a ON a.id = j.id""").fetchall()
    if not rows:
        print("Nothing judged yet.")
        return
    right = [r for r in rows if r[3]]
    wrong = [r for r in rows if not r[3]]
    print(f"{len(rows)} attempts judged ({len(wrong)} where Qwen's answer is wrong, {len(right)} right)")
    if right:
        zero = sum(r[1] == 0 for r in right)
        print(f"- Qwen right: the judge says 'no mistake' in {zero}/{len(right)}")
    if wrong:
        zero = sum(r[1] == 0 for r in wrong)
        print(f"- Qwen wrong: the judge says 'no mistake' (impossible) in {zero}/{len(wrong)}")
    calc_cases = agree = 0
    for _id, step, _c, _qc, steps in wrong:
        steps = json.loads(steps)
        first_false = next((i for i, s in enumerate(steps, 1) if check_calculations([s])[1]), None)
        if first_false:
            calc_cases += 1
            agree += 0 < step <= first_false
    if calc_cases:
        print(f"- With a provably false calculation: the judge points at it or earlier in {agree}/{calc_cases}")
    spread = Counter(r[1] for r in wrong)
    print(f"- Steps it picks when Qwen is wrong: {dict(sorted(spread.items()))}")
    confs = [r[2] for r in wrong if r[2] is not None]
    if confs:
        high = sum(c >= 0.6 for c in confs)
        print(f"- Confidence: mean {sum(confs) / len(confs):.2f}; {high}/{len(confs)} at 0.6 or more")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/teach.db")
    ap.add_argument("--port", type=int, default=8084)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    db = sqlite3.connect(args.db)
    db.execute(SCHEMA)
    if args.report:
        report(db)
        return
    url = f"http://127.0.0.1:{args.port}/v1/chat/completions"
    try:
        # Only references whose calculations were actually checked: the pilot showed
        # that "right answer, no calculation" is usually an empty plan
        refs = dict(db.execute("SELECT id, steps FROM guided WHERE ok = 1 AND checks > 0"))
    except sqlite3.OperationalError:
        refs = {}
    # Qwen-right attempts first: they are the check that needs no labels
    rows = db.execute("""SELECT a.id, a.text, a.correct_value, a.steps FROM attempts a
                         LEFT JOIN judged j ON j.id = a.id
                         WHERE j.id IS NULL AND a.status = 'ok' AND json_array_length(a.steps) BETWEEN 1 AND 9
                         ORDER BY a.qwen_correct DESC, a.rowid""").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows)} attempts to judge, {len(refs)} reference solutions available", flush=True)
    for i, (pid, text, correct, steps) in enumerate(rows, 1):
        steps = json.loads(steps)
        reference = json.loads(refs[pid]) if pid in refs else None
        start = time.perf_counter()
        try:
            step, conf, probs, timings = ask(url, text, correct, steps, reference, args.timeout)
        except Exception as error:  # noqa: BLE001
            print(f"[{i}/{len(rows)}] {pid} error: {error}", flush=True)
            continue
        seconds = time.perf_counter() - start
        db.execute("INSERT OR REPLACE INTO judged VALUES (?,?,?,?,?,?,?)",
                   (pid, step, conf, json.dumps(probs), int(bool(reference)), round(seconds, 1),
                    timings.get("prompt_n", 0)))
        db.commit()
        print(f"[{i}/{len(rows)}] {pid}: step {step} of {len(steps)} (p={conf}) "
              f"{'with' if reference else 'without'} reference, {seconds:.0f} s", flush=True)
    print()
    report(db)
    db.close()


if __name__ == "__main__":
    main()

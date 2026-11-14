#!/usr/bin/env python3
"""Ask Qwen to solve M3Kang problems step by step, ahead of time, and store the
attempts in SQLite. The "Correct Qwen" mode shows these attempts to students.

    python3 teach/generate_solutions.py --limit 10          # pilot
    python3 teach/generate_solutions.py                     # all candidates (hours)

Only problems that are text only (Qwen can't see figures) and whose five options
are distinct numbers are used, so the final answer can be checked in code
against options[label]. Run it again to resume: done problems are skipped.
Standard library only, so it runs on the board.
"""
import argparse
import json
import os
import random
import re
import sqlite3
import sys
import time
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from copilot.client import post  # noqa: E402
from copilot.config import CONFIG  # noqa: E402

URL = f"http://127.0.0.1:{CONFIG['LLM_PORT']}/v1/chat/completions"
MAX_STEPS = 6

# Stable system prompt: llama-server caches it, so it's only read once.
SYSTEM = (
    "You are a student solving a math competition problem. Solve it in 2 to 6 numbered steps. "
    "Every step must use the actual numbers of the problem: state a fact you deduce, or do one "
    "calculation and write its result. Never describe a plan without doing it. Plain text, no LaTeX. "
    "Then give the final number.\n\n"
    "Example problem: A farmer has 3 pens with 4 sheep each, and sells 5 sheep. How many sheep are left?\n"
    "Step 1: There are 3 pens with 4 sheep each, so 3 x 4 = 12 sheep.\n"
    "Step 2: He sells 5 sheep, so 12 - 5 = 7 sheep are left.\n"
    "Answer: 7")

# The grammar forces the format: 2 to 6 lines "Step N: ...", then "Answer: <number>".
# Lines can't contain $ or backslashes, which rules out LaTeX.
GRAMMAR = r'''
root   ::= step step step? step? step? step? answer
step   ::= "Step " [1-6] ": " [^\n$\\]{1,320} "\n"
answer ::= "Answer: " "-"? [0-9]{1,7} ("." [0-9]{1,4})? ("/" [0-9]{1,4})?
'''

SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    language TEXT, level INTEGER, year INTEGER,
    text TEXT,              -- statement without the question number
    options TEXT,           -- JSON, from OCR
    label TEXT,             -- correct letter
    correct_value TEXT,     -- options[label]
    steps TEXT,             -- JSON list of step strings, as Qwen wrote them
    qwen_answer TEXT,       -- the number after "Answer:"
    qwen_correct INTEGER,   -- 1 if qwen_answer == correct_value
    qwen_letter TEXT,       -- the option letter Qwen's number matches, if any
    seconds REAL, prompt_n INTEGER, predicted_n INTEGER,
    status TEXT
)"""

NUMBER = re.compile(r"-?\d+([.,]\d+)?$")


def to_number(text):
    """'12', '2,5', '3/4' -> Fraction, or None."""
    text = text.strip().replace(",", ".")
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


def candidates(paths, language):
    problems = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            problems += [json.loads(line) for line in f if line.strip()]
    keep = []
    for p in problems:
        opts = p.get("options") or {}
        if (p["language"] == language and p["text_only"] and len(opts) == 5
                and all(NUMBER.match(v.strip()) for v in opts.values())
                and len(set(opts.values())) == 5):
            keep.append(p)
    return keep


def clean_text(text):
    return re.sub(r"^\s*\d+\.\s*", "", text).strip()


def attempt(problem, timeout):
    start = time.perf_counter()
    response = post(URL, {
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": clean_text(problem["text"])}],
        "grammar": GRAMMAR,
        "max_tokens": 600,
        "temperature": 0,
        "seed": int(CONFIG["SEED"]),
        "cache_prompt": True,
    }, timeout=timeout)
    content = response["choices"][0]["message"]["content"]
    timings = response.get("timings", {})
    steps = [line.split(": ", 1)[1].strip() for line in content.splitlines()
             if line.startswith("Step ") and ": " in line]
    match = re.search(r"Answer:\s*(\S+)", content)
    answer = match.group(1) if match else ""
    return steps, answer, time.perf_counter() - start, timings


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--inputs", default="data/train.jsonl,data/val.jsonl")
    p.add_argument("--db", default="data/teach.db")
    p.add_argument("--language", default="eng")
    p.add_argument("--limit", type=int, default=0, help="0 means every candidate")
    p.add_argument("--timeout", type=float, default=180)
    p.add_argument("--only-worked", action="store_true",
                   help="only problems the math model solved (generate_worked.py), so they can be played "
                        "in 'Corrige a Qwen' too")
    args = p.parse_args()

    problems = candidates(args.inputs.split(","), args.language)
    random.Random(42).shuffle(problems)  # the pilot is a fixed, mixed sample
    if args.only_worked:
        with sqlite3.connect(args.db) as db:
            solved = {r[0] for r in db.execute("SELECT id FROM worked WHERE ok = 1")}
        problems = [x for x in problems if x["id"] in solved]
    if args.limit:
        problems = problems[:args.limit]

    db = sqlite3.connect(args.db)
    db.execute(SCHEMA)
    done = {row[0] for row in db.execute("SELECT id FROM attempts")}
    todo = [x for x in problems if x["id"] not in done]
    print(f"{len(problems)} candidates, {len(done)} already in {args.db}, {len(todo)} to do", flush=True)

    for i, prob in enumerate(todo, 1):
        correct_value = prob["options"][prob["label"]]
        status = "ok"
        steps, answer, seconds, timings = [], "", 0.0, {}
        try:
            steps, answer, seconds, timings = attempt(prob, args.timeout)
        except Exception as error:  # noqa: BLE001 - keep going, record the failure
            status = f"error: {error}"
        value = to_number(answer) if answer else None
        right = value is not None and value == to_number(correct_value)
        letter = next((k for k, v in prob["options"].items()
                       if value is not None and to_number(v) == value), "")
        db.execute("INSERT OR REPLACE INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            prob["id"], prob["language"], prob["level"], prob["year"], clean_text(prob["text"]),
            json.dumps(prob["options"]), prob["label"], correct_value, json.dumps(steps),
            answer, int(right), letter, round(seconds, 1),
            timings.get("prompt_n", 0), timings.get("predicted_n", 0), status))
        db.commit()
        print(f"[{i}/{len(todo)}] {prob['id']} L{prob['level']}: qwen {answer or '-'} "
              f"(option {letter or 'none'}), correct {correct_value} -> {'RIGHT' if right else 'wrong'}, "
              f"{len(steps)} steps, {seconds:.0f} s, {status}", flush=True)

    rows = db.execute("SELECT COUNT(*), SUM(qwen_correct), AVG(seconds), SUM(qwen_letter != ''), "
                      "SUM(status != 'ok') FROM attempts").fetchone()
    n, right, mean_s, on_option, errors = rows
    if n:
        print(f"\nIn {args.db}: {n} attempts, Qwen right {right}/{n} ({100 * right / n:.0f}%), "
              f"answer is one of the 5 options {on_option}/{n}, errors {errors}, mean {mean_s:.0f} s")
    db.close()


if __name__ == "__main__":
    main()

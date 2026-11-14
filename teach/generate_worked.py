#!/usr/bin/env python3
"""Worked solutions for the hints: the math model solves each problem for real.

    python3 teach/generate_worked.py --limit 10      # pilot
    python3 teach/generate_worked.py                 # every problem in data/teach.db

Qwen2.5-Math-1.5B-Instruct (port 8084) gets only the problem, as it was
trained: "reason step by step, and put your final answer within \\boxed{}".
It is NOT told the answer, so a solution whose boxed answer equals the correct
option was really solved. Only those become hints: their steps, shown one at
a time, never the last one.

What we learnt before this: forcing a format with a grammar made the model
write empty plans, and telling it the answer made it copy the answer without
reasoning. Free reasoning was the only thing that worked.
Run it again to resume. Standard library only.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from copilot.client import post  # noqa: E402
from copilot.config import CONFIG  # noqa: E402
from teach.game import evaluate  # noqa: E402
from teach.generate_solutions import candidates, clean_text  # noqa: E402

SYSTEM = "Please reason step by step, and put your final answer within \\boxed{}."

SCHEMA = """
CREATE TABLE IF NOT EXISTS worked (
    id TEXT PRIMARY KEY,
    raw TEXT,            -- the model's text, as written
    steps TEXT,          -- JSON list of plain-text steps, without the final answer
    answer TEXT,         -- what was inside the last \\boxed{}
    ok INTEGER,          -- 1 the boxed answer equals the correct option
    seconds REAL, prompt_n INTEGER, predicted_n INTEGER
)"""


def boxed(text):
    """Content of the last \\boxed{...}, with nested braces."""
    start = text.rfind("\\boxed{")
    if start < 0:
        return ""
    i, depth, out = start + len("\\boxed{"), 1, []
    while i < len(text) and depth:
        ch = text[i]
        depth += ch == "{"
        depth -= ch == "}"
        if depth:
            out.append(ch)
        i += 1
    return "".join(out).strip()


def plain(tex):
    """LaTeX the model writes -> text a student can read."""
    s = tex
    s = re.sub(r"\\d?frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", s)
    s = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", s)
    s = re.sub(r"\\binom\{([^{}]*)\}\{([^{}]*)\}", r"C(\1, \2)", s)
    s = re.sub(r"\\[pb]?mod\{([^{}]*)\}", r"(mod \1)", s)
    s = re.sub(r"\\[pb]mod\s*(\d+)", r"(mod \1)", s)
    s = re.sub(r"\\text\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\boxed\{([^{}]*)\}", r"\1", s)
    for a, b in [("\\equiv", "≡"), ("\\mod", "mod"), ("\\times", "×"), ("\\cdot", "·"), ("\\div", "÷"), ("\\leq", "≤"), ("\\geq", "≥"), ("\\le", "≤"),
                 ("\\ge", "≥"), ("\\neq", "≠"), ("\\ldots", "…"), ("\\dots", "…"), ("\\pi", "π"), ("\\%", "%"),
                 ("\\quad", " "), ("\\,", " "), ("\\left", ""), ("\\right", ""), ("\\(", ""), ("\\)", ""),
                 ("\\[", ""), ("\\]", ""), ("$", ""), ("**", "")]:
        s = s.replace(a, b)
    s = re.sub(r"\\[a-zA-Z]+", "", s)          # any command left
    s = re.sub(r"(?m)^#+\s*", "", s)           # markdown headings
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def split_steps(raw):
    """Paragraphs of the solution as steps; the part with the boxed answer is dropped."""
    text = raw[:raw.rfind("\\boxed{")] if "\\boxed{" in raw else raw
    parts = [plain(p) for p in re.split(r"\n\s*\n", text)]
    parts = [p for p in parts if p.strip()]  # keep short ones: "5x + 6y = 43" is 12 characters
    # Glue an equation-only paragraph to the sentence before it, so each step reads as one idea
    steps = []
    for p in parts:
        if steps and (len(p) < 40 or not re.search(r"[a-zA-Z]{3}", p)):
            steps[-1] += " " + p
        else:
            steps.append(p)
    return steps


def same(a, correct):
    a = plain(a).replace(" ", "")
    try:
        return evaluate(a) == evaluate(correct)
    except ValueError:
        return a == correct.replace(" ", "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/teach.db")
    ap.add_argument("--port", type=int, default=8084)
    ap.add_argument("--limit", type=int, default=0)
    # Solved problems needed 250-500 tokens in the pilot; the longest runs were failures
    ap.add_argument("--max-tokens", type=int, default=550)
    ap.add_argument("--order", default="", help="JSON list of ids from plan_order.py (default: Qwen 0.8B attempts)")
    ap.add_argument("--inputs", default="data/train.jsonl,data/val.jsonl")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--reparse", action="store_true", help="recompute steps from the stored text, no model calls")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/v1/chat/completions"

    db = sqlite3.connect(args.db)
    db.execute(SCHEMA)
    if args.reparse:
        for pid, raw in db.execute("SELECT id, raw FROM worked").fetchall():
            db.execute("UPDATE worked SET steps = ? WHERE id = ?", (json.dumps(split_steps(raw), ensure_ascii=False), pid))
        db.commit()
        print("Steps recomputed from the stored solutions")
        return
    done = {r[0] for r in db.execute("SELECT id FROM worked")}
    if args.order:
        # The order from plan_order.py, over every candidate, with or without a Qwen 0.8B attempt
        by_id = {p["id"]: p for p in candidates(args.inputs.split(","), "eng")}
        with open(args.order) as f:
            order = json.load(f)
        rows = [(pid, clean_text(by_id[pid]["text"]), by_id[pid]["options"][by_id[pid]["label"]])
                for pid in order if pid in by_id and pid not in done]
    else:
        rows = db.execute("SELECT a.id, a.text, a.correct_value FROM attempts a LEFT JOIN worked w ON w.id = a.id "
                          "WHERE w.id IS NULL AND a.status = 'ok' ORDER BY a.rowid").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows)} problems to solve", flush=True)
    for i, (pid, text, correct) in enumerate(rows, 1):
        start = time.perf_counter()
        raw, timings = "", {}
        try:
            response = post(url, {
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
                "max_tokens": args.max_tokens, "temperature": 0, "seed": int(CONFIG["SEED"]),
            }, timeout=args.timeout)
            raw = response["choices"][0]["message"]["content"]
            timings = response.get("timings", {})
        except Exception as error:  # noqa: BLE001
            print(f"[{i}/{len(rows)}] {pid} error: {error}", flush=True)
            continue
        answer = boxed(raw)
        ok = bool(answer) and same(answer, correct)
        steps = split_steps(raw)
        seconds = time.perf_counter() - start
        db.execute("INSERT OR REPLACE INTO worked VALUES (?,?,?,?,?,?,?,?)",
                   (pid, raw, json.dumps(steps, ensure_ascii=False), answer, int(ok), round(seconds, 1),
                    timings.get("prompt_n", 0), timings.get("predicted_n", 0)))
        db.commit()
        print(f"[{i}/{len(rows)}] {pid}: model {answer or '(no answer)'} vs correct {correct} -> "
              f"{'SOLVED' if ok else 'wrong'}, {len(steps)} steps, {seconds:.0f} s", flush=True)
    n, ok = db.execute("SELECT COUNT(*), SUM(ok) FROM worked").fetchone()
    print(f"\nSolved for real: {ok}/{n}")
    db.close()


if __name__ == "__main__":
    main()

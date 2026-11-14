#!/usr/bin/env python3
"""Decide in which order the math model works through the problems, so every
topic and tier gets its share of verified hints.

    python3 teach/plan_order.py              # -> data/worked_order.json

Every candidate (text only, five distinct numeric options) gets its topic with
the embeddings, the same way prepare_app.py does, and its tier from the level.
The order then takes turns between the 18 cells (6 topics x 3 tiers): the
first problem of every cell, then the second of every cell, and so on, the
easiest level first inside each cell. Problems already in the worked table go
last, so a rerun picks up where it left off.
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from copilot.embed import embed  # noqa: E402
from teach.generate_solutions import candidates, clean_text  # noqa: E402
from teach.kinds import TYPES, tier_of  # noqa: E402


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs", default="data/train.jsonl,data/val.jsonl")
    ap.add_argument("--db", default="data/teach.db")
    ap.add_argument("--out", default="data/worked_order.json")
    args = ap.parse_args()

    problems = candidates(args.inputs.split(","), "eng")
    type_vecs = [(t["key"], embed(t["describe"], query=True)) for t in TYPES]
    cells = defaultdict(list)
    for i in range(0, len(problems), 16):
        chunk = problems[i:i + 16]
        for p, vec in zip(chunk, embed([clean_text(p["text"]) for p in chunk])):
            kind = max(type_vecs, key=lambda kv: max(dot(vec, v) for v in kv[1]))[0]
            cells[(kind, tier_of(p["level"]))].append(p)
        print(f"\r{min(i + 16, len(problems))}/{len(problems)} classified", end="", flush=True)
    print()

    db = sqlite3.connect(args.db)
    try:
        done = {r[0] for r in db.execute("SELECT id FROM worked")}
    except sqlite3.OperationalError:
        done = set()
    db.close()

    queues = {c: sorted((p for p in ps if p["id"] not in done), key=lambda p: (p["level"], p["id"]))
              for c, ps in cells.items()}
    order = []
    while any(queues.values()):
        for c in sorted(queues):
            if queues[c]:
                order.append(queues[c].pop(0)["id"])
    with open(args.out, "w") as f:
        json.dump(order, f)

    print(f"{len(order)} problems to do, {len(done)} already done. Candidates per topic and tier:")
    for t in TYPES:
        row = [len(cells.get((t["key"], tier), [])) for tier in (1, 2, 3)]
        print(f"  {t['name']:10} fácil {row[0]:3}  medio {row[1]:3}  difícil {row[2]:3}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Error analysis of one eval run: accuracy by topic, level, language and
where the answer came from. Runs on the board (it needs the embedding server).

    python3 analyse_errors.py results/eval-<time>-<name>.csv --problems data/val.jsonl

M3Kang has no topic field. Each problem gets the topic of the MateQwen app
(teach/kinds.py): the topic whose descriptions are closest to the problem in
the bge-small embeddings, the same rule teach/prepare_app.py uses.
"""
import argparse
import csv
import json
import os
import sys
from collections import defaultdict

from copilot.embed import embed

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "teach"))
from kinds import TYPES  # noqa: E402


def topics(problems):
    type_vecs = [(t["key"], embed(t["describe"], query=True)) for t in TYPES]
    out = {}
    for i in range(0, len(problems), 16):
        chunk = problems[i:i + 16]
        for p, vec in zip(chunk, embed([p["text"] for p in chunk])):
            scores = {k: max(sum(a * b for a, b in zip(vec, v)) for v in vecs) for k, vecs in type_vecs}
            out[(p["id"], p["language"])] = max(scores, key=scores.get)
    return out


def table(title, rows, key):
    groups = defaultdict(list)
    for r in rows:
        groups[key(r)].append(int(r["correct"]))
    print(f"\n{title}")
    print("| | Correct | Problems | Accuracy |\n| --- | --- | --- | --- |")
    for k in sorted(groups, key=str):
        hits = groups[k]
        print(f"| {k} | {sum(hits)} | {len(hits)} | {100 * sum(hits) / len(hits):.0f}% |")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv")
    p.add_argument("--problems", default="data/val.jsonl")
    args = p.parse_args()
    with open(args.problems, encoding="utf-8") as f:
        problems = {(x["id"], x["language"]): x for x in map(json.loads, f)}
    with open(args.csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    topic = topics([problems[(r["id"], r["language"])] for r in rows])
    for r in rows:
        r["topic"] = topic[(r["id"], r["language"])]
    total = sum(int(r["correct"]) for r in rows)
    print(f"{args.csv}: {total}/{len(rows)} = {100 * total / len(rows):.1f}%")
    table("By topic", rows, lambda r: r["topic"])
    table("By level", rows, lambda r: int(r["level"]))
    table("By language", rows, lambda r: r["language"])
    table("By whether the options are text", rows, lambda r: r["has_options"])
    if rows and "source" in rows[0]:
        table("By where the answer came from", rows, lambda r: r["source"] or r["status"])


if __name__ == "__main__":
    main()

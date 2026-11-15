#!/usr/bin/env python3
"""Choose PRIOR_WEIGHT on the laptop, from one eval run on the board.

    python3 tune_prior.py results/eval-<time>-combo.csv

eval.py saves the letter model's probabilities for every problem, so the
vote (model probabilities x prior^weight) can be replayed for any weight
without running a model again. Answers that came from the memory, a program
or a checker with one option are kept as they were; a checker that left
several options keeps them, and the vote picks among them.
"""
import argparse
import csv
import json

from copilot import priors


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv")
    p.add_argument("--problems", default="data/val.jsonl")
    args = p.parse_args()

    with open(args.problems, encoding="utf-8") as f:
        problems = {(x["id"], x["language"]): x for x in map(json.loads, f) if x}
    with open(args.csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    prior = priors.load()

    print(f"{len(rows)} problems. Accuracy for each weight (0 = the model alone):")
    for weight in (0, 0.25, 0.5, 0.75, 1, 1.5, 2, 3):
        hits = 0
        for r in rows:
            problem = problems[(r["id"], r["language"])]
            source = r.get("source", "")
            probs = json.loads(r.get("probs") or "{}")
            if source in ("memory", "code", "check") or not probs:
                answer = r["answer"]
            else:
                vote = priors.combine(probs, prior(problem), weight)
                answer = priors.best(vote, r.get("passed") or priors.LETTERS)
            hits += answer == problem["label"]
        print(f"  {weight:4}: {hits}/{len(rows)} = {100 * hits / len(rows):.1f}%")
    print("Put the best weight in config.env as PRIOR_WEIGHT.")


if __name__ == "__main__":
    main()

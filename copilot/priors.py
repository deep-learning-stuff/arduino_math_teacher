"""What we know about the answer before any model reads the problem.

Kangaroo setters hide the correct option among distractors that sit around
it: when the five options are numbers, sorted from smallest to largest, the
correct one is the middle one far more often than the smallest (29% against
10% on the training set). When the options are pictures, D is the most
common letter. These counts, from data/train.jsonl, are the prior: the fallback
when every model fails, and one of the two factors of the final vote.
"""
import json
import math
import os
from collections import Counter

from .cascade import fix_option, numeric_options
from .config import CONFIG, ROOT

LETTERS = "ABCDE"
SMOOTH = 2.0  # pseudo-counts per cell, so a rare cell never gets probability 0


def kind(problem):
    """('rank', None) for five distinct numbers, ('text', None) for other
    written options, ('picture', None) when the options are a picture.
    A letter prior per level was tried for pictures and lost in 5-fold
    cross-validation on train (116 against 152 of 555): too few per level."""
    options = problem.get("options") or {}
    if not options:
        return "picture", None
    if numeric_options({k: fix_option(v) for k, v in options.items()}):
        return "rank", None
    return "text", None


def ranks(problem):
    """{letter: position 0..4 of its value, smallest first}."""
    values = numeric_options({k: fix_option(v) for k, v in problem["options"].items()})
    order = sorted(values, key=lambda k: values[k])
    return {letter: order.index(letter) for letter in LETTERS}


class Prior:
    def __init__(self, problems):
        self.counts = {}
        for p in problems:
            if p.get("label") not in LETTERS:
                continue
            group, sub = kind(p)
            cell = ranks(p)[p["label"]] if group == "rank" else p["label"]
            self.counts.setdefault((group, sub), Counter())[cell] += 1

    def __call__(self, problem):
        """{letter: probability}, summing to 1."""
        group, sub = kind(problem)
        counts = self.counts.get((group, sub)) or self.counts.get((group, None)) or Counter()
        if group == "rank":
            r = ranks(problem)
            raw = {letter: counts[r[letter]] + SMOOTH for letter in LETTERS}
        else:
            raw = {letter: counts[letter] + SMOOTH for letter in LETTERS}
        total = sum(raw.values())
        return {letter: v / total for letter, v in raw.items()}


def load(paths=None):
    """The prior from the training files in PRIOR_FILES (comma separated)."""
    paths = paths or [os.path.join(ROOT, x) for x in
                      CONFIG.get("PRIOR_FILES", "data/train.jsonl").split(",") if x]
    problems = []
    for path in paths:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                problems += [json.loads(line) for line in f if line.strip()]
    return Prior(problems)


def combine(llm_probs, prior, weight):
    """Posterior over letters: llm^1 * prior^weight, renormalised. With no
    model probabilities, the prior alone."""
    scores = {}
    for letter in LETTERS:
        lp = math.log(max(llm_probs.get(letter, 0.0), 1e-6)) if llm_probs else 0.0
        scores[letter] = lp + weight * math.log(prior[letter])
    top = max(scores.values())
    exp = {k: math.exp(v - top) for k, v in scores.items()}
    total = sum(exp.values())
    return {k: v / total for k, v in exp.items()}


def best(dist, allowed=LETTERS):
    """Most probable letter among allowed; ties go to the earlier letter."""
    return max(allowed, key=lambda k: (dist[k], -LETTERS.index(k)))

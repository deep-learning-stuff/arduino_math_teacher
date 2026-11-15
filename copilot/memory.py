"""The Kangaroo remembers: the same problem often comes back.

The competition reuses problems between neighbouring levels of the same year,
and from one year to another, sometimes with the options in a different
order. So before any model runs, we look for the problem among the solved
ones. A match only counts when its correct VALUE is one of today's options:
the letter alone is not enough, because the options get shuffled.

The search is lexical (words and characters), not an embedding: it needs no
server, takes milliseconds, and "almost the same text" is exactly what a
reused problem looks like.
"""
import difflib
import json
import os
import re

from .cascade import fix_option, to_number
from .config import CONFIG, ROOT

WORD = re.compile(r"[a-z0-9]+")
THRESHOLD = float(CONFIG.get("MEMORY_THRESHOLD", "0.90"))
SHORTLIST = 8


def normalize(text):
    text = re.sub(r"^\s*\d+\.\s*", "", text or "").lower()
    return " ".join(WORD.findall(text))


def same_value(a, b):
    na, nb = to_number(fix_option(a)), to_number(fix_option(b))
    if na is not None and nb is not None:
        return na == nb
    return normalize(str(a)) == normalize(str(b)) and normalize(str(a)) != ""


class Memory:
    def __init__(self, problems):
        self.rows = []
        for p in problems:
            if p.get("label") not in "ABCDE" or not p.get("text"):
                continue
            norm = normalize(p["text"])
            if len(norm) < 40:  # too short to tell problems apart
                continue
            self.rows.append((norm, set(norm.split()), p))

    def lookup(self, problem, exclude_id=None):
        """(letter, info) for a remembered problem whose answer is among the
        options, else (None, info). info has the best similarity found."""
        norm = normalize(problem.get("text"))
        words = set(norm.split())
        if len(norm) < 40 or not words:
            return None, {"similarity": 0.0}
        scored = []
        for text, w, p in self.rows:
            if exclude_id and p.get("id") == exclude_id:
                continue
            union = len(words | w)
            if union:
                scored.append((len(words & w) / union, text, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        best = (0.0, None)
        for _j, text, p in scored[:SHORTLIST]:
            ratio = difflib.SequenceMatcher(None, norm, text, autojunk=False).ratio()
            if ratio > best[0]:
                best = (ratio, p)
        ratio, p = best
        info = {"similarity": round(ratio, 3), "match": p["id"] if p else ""}
        if p is None or ratio < THRESHOLD:
            return None, info
        options = problem.get("options") or {}
        known = (p.get("options") or {}).get(p["label"])
        if options and known is not None:
            hits = [k for k, v in options.items() if same_value(v, known)]
            return (hits[0] if len(hits) == 1 else None), info
        # Options in a picture: the letter alone is not trusted. Measured on
        # val, an identical statement had a different letter (shuffled picture).
        return None, info


def load(paths=None):
    paths = paths or [os.path.join(ROOT, x) for x in
                      CONFIG.get("MEMORY_FILES", "data/train.jsonl,data/val.jsonl").split(",") if x]
    problems = []
    for path in paths:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                problems += [json.loads(line) for line in f if line.strip()]
    return Memory(problems)

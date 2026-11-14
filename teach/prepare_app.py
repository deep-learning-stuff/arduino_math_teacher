#!/usr/bin/env python3
"""Turn Qwen's stored attempts into the problems of the "Correct Qwen" game.

    python3 teach/prepare_app.py          # data/teach.db + data/index.db -> data/app.db

For every attempt it keeps the usable ones (at least 2 steps, most of them with
numbers), and adds with the embedding model:
  - its type: the closest of the six types in teach/kinds.py
  - its tier: from the M3Kang level
  - a similar solved problem from the training set, for the second hint
Run it again whenever generate_solutions.py has added attempts. Student data in
data/app.db is never touched.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from copilot.embed import embed  # noqa: E402
from teach.game import connect, evaluate  # noqa: E402
from teach.generate_solutions import candidates, clean_text  # noqa: E402
from teach.kinds import TYPES, tier_of  # noqa: E402

DIGIT = re.compile(r"\d")
MAX_GUIDE = 5       # at most this many steps of a worked solution as hints
MIN_SIBLING = 0.75  # cosine similarity; unrelated pairs we saw were at 0.63-0.65, a good one at 0.91


def usable(steps):
    with_numbers = sum(bool(DIGIT.search(s)) for s in steps)
    return len(steps) >= 2 and with_numbers * 2 >= len(steps)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def load_index(path):
    rows = []
    db = sqlite3.connect(path)
    for _key, problem, blob in db.execute("SELECT key, problem, vec FROM problems"):
        p = json.loads(problem)
        if not p.get("options") or p["label"] not in p["options"]:
            continue  # the hint shows the neighbour's answer, so it needs one
        vec = array("f")
        vec.frombytes(blob)
        rows.append((p, vec))
    db.close()
    return rows


RESULT = re.compile(r"=\s*(-?\d+(?:[.,]\d+)?)(?![\d/])")


def guide_steps(steps, correct):
    """The steps of a worked solution that can be shown as hints.

    Never the last step, and never a step that already states the answer
    ("... = 9"): the hints stop just before it, so the student does the end.
    """
    try:
        answer = evaluate(correct)
    except ValueError:
        answer = None
    def gives_answer(text):
        for v in RESULT.findall(text):
            try:
                if answer is not None and evaluate(v) == answer:
                    return True
            except ValueError:
                pass
        return False

    out = []
    for step in steps[:-1]:
        if gives_answer(step):
            # Keep the reasoning before the line that states the answer, then stop
            kept = []
            for line in step.split("\n"):
                if gives_answer(line):
                    break
                kept.append(line)
            head = "\n".join(kept).strip()
            if len(head) > 40:
                out.append(head[:700])
            break
        out.append(step[:700])
    return out[:MAX_GUIDE]


def clean(text):
    """Drop the question number at the start, such as '22. '."""
    return re.sub(r"^\s*\d+\.\s*", "", text).strip()


def load_texts(paths):
    """{id: text} from M3Kang jsonl files in another language. Missing files are fine."""
    texts = {}
    for path in paths:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        p = json.loads(line)
                        texts[p["id"]] = clean(p["text"])
    return texts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--attempts", default="data/teach.db")
    ap.add_argument("--index", default="data/index.db")
    ap.add_argument("--db", default="data/app.db")
    ap.add_argument("--catalan", default="data/cat/train.jsonl,data/cat/val.jsonl",
                    help="M3Kang in Catalan (prepare_m3kang.py --languages cat --no-ocr --out data/cat): "
                         "the page shows these texts, matched by id; everything else works in English")
    ap.add_argument("--hints-table", default="", choices=["", "hints", "hints_answer"],
                    help="which Qwen hints to show (none until a pilot has been judged by hand)")
    ap.add_argument("--inputs", default="data/train.jsonl,data/val.jsonl")
    ap.add_argument("--twin-min", type=float, default=0.85,
                    help="cosine similarity for two problems of the same topic to count as twins")
    ap.add_argument("--require-guide", action="store_true",
                    help="only problems with a verified worked solution (generate_worked.py)")
    args = ap.parse_args()
    start = time.perf_counter()
    catalan = load_texts(args.catalan.split(","))
    print(f"{len(catalan)} problems in Catalan")

    src = sqlite3.connect(args.attempts)
    attempts = src.execute("SELECT id, level, year, text, options, label, correct_value, steps, "
                           "qwen_answer, qwen_correct, qwen_letter FROM attempts WHERE status = 'ok'").fetchall()
    hints = {}
    if args.hints_table:  # hints from generate_hints.py, only the ones that passed the checks in code
        hints = dict(src.execute(f"SELECT id, hint FROM {args.hints_table} WHERE ok = 1"))
    # Problems the math model solved but Qwen 0.8B never tried: they can be played
    # in "Resuélvelo tú" (they have hints) but not in "Corrige a Qwen" (no attempt)
    tried = {a[0] for a in attempts}
    try:
        solved_ids = [r[0] for r in src.execute("SELECT id FROM worked WHERE ok = 1")]
    except sqlite3.OperationalError:
        solved_ids = []
    by_id = {p["id"]: p for p in candidates(args.inputs.split(","), "eng")}
    solve_only = set()
    for pid in solved_ids:
        if pid not in tried and pid in by_id:
            p = by_id[pid]
            attempts.append((pid, p["level"], p["year"], clean_text(p["text"]), json.dumps(p["options"]),
                             p["label"], p["options"][p["label"]], "[]", "", 0, ""))
            solve_only.add(pid)
    correct = {a[0]: a[6] for a in attempts}
    guides = {}
    try:  # worked solutions from generate_worked.py, only the ones the model really solved
        for pid, steps in src.execute("SELECT id, steps FROM worked WHERE ok = 1"):
            steps = guide_steps(json.loads(steps), correct.get(pid, ""))
            if steps:
                guides[pid] = json.dumps(steps, ensure_ascii=False)
    except sqlite3.OperationalError:
        pass
    src.close()
    keep = [a for a in attempts if a[0] in solve_only or usable(json.loads(a[7]))]
    if args.require_guide:
        keep = [a for a in keep if a[0] in guides]
    print(f"{len(tried)} Qwen attempts, {len(solve_only)} solve-only problems, {len(keep)} kept, "
          f"{len(hints)} Qwen hints, {len(guides)} verified worked solutions")

    # Each type is described by a few sentences; a problem's score for a type is
    # its best similarity to one of them.
    type_vecs = [(t["key"], embed(t["describe"], query=True)) for t in TYPES]
    index = load_index(args.index)
    # The topic of every training problem, so a sibling is always from the same topic
    index_kind = [max(type_vecs, key=lambda kv: max(dot(v, t) for t in kv[1]))[0] for _p, v in index]
    print(f"{len(index)} training problems with answers for the similar-problem hint")

    db = connect(args.db)
    if args.require_guide:  # the game then only offers problems with verified hints
        ids = [a[0] for a in keep]
        db.execute(f"DELETE FROM problems WHERE id NOT IN ({','.join('?' * len(ids))})", ids)
    counts = {}
    vectors_of = {}  # id -> (topic, vector), for the twins
    for i in range(0, len(keep), 16):
        chunk = keep[i:i + 16]
        vectors = embed([a[3] for a in chunk])
        for a, vec in zip(chunk, vectors):
            pid, level = a[0], a[1]
            scores = {key: max(dot(vec, v) for v in vecs) for key, vecs in type_vecs}
            kind = max(scores, key=scores.get)
            vectors_of[pid] = (kind, vec)
            # The sibling: the most similar solved problem of a LOWER level, so it's
            # easier; only level-0 problems take a sibling of the same level.
            # Same topic, and similar enough: below MIN_SIBLING the pairs we looked at
            # only shared words ("Kangaroo", "zoo"), not the idea, so no sibling is offered.
            best = None
            for (p, v), p_kind in zip(index, index_kind):
                if p["id"] == pid or p_kind != kind or p["level"] > level or (p["level"] == level and level > 0):
                    continue
                s = dot(vec, v)
                if best is None or s > best[0]:
                    best = (s, p)
            similar = None
            if best and best[0] >= MIN_SIBLING:
                p = best[1]
                similar = json.dumps({"id": p["id"], "level": p["level"], "score": round(best[0], 3),
                                      "text": clean(p["text"]), "text_ca": catalan.get(p["id"]),
                                      "answer": p["options"][p["label"]]}, ensure_ascii=False)
            db.execute("INSERT OR REPLACE INTO problems (id, level, tier, year, type, type_score, text, options, "
                       "label, correct_value, steps, qwen_answer, qwen_correct, qwen_letter, similar, hint, text_ca, guide) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                           pid, level, tier_of(level), a[2], kind, round(scores[kind], 3), a[3], a[4], a[5],
                           a[6], a[7], a[8], a[9], a[10], similar, hints.get(pid), catalan.get(pid), guides.get(pid)))
            counts[(kind, tier_of(level))] = counts.get((kind, tier_of(level)), 0) + 1
        db.commit()
        print(f"\r{min(i + 16, len(keep))}/{len(keep)} problems prepared", end="", flush=True)

    # Twins: other problems of the game, same topic, almost the same problem.
    # Only very close pairs: the embeddings are reliable for near-duplicates
    # (the two chess problems scored 0.91), not for loose likeness.
    with_twins = 0
    for pid, (kind, vec) in vectors_of.items():
        near = sorted(((dot(vec, v2), q) for q, (k2, v2) in vectors_of.items()
                       if q != pid and k2 == kind), reverse=True)
        twins = [q for s, q in near if s >= args.twin_min][:3]
        with_twins += bool(twins)
        db.execute("UPDATE problems SET twins = ? WHERE id = ?", (json.dumps(twins), pid))
    db.commit()
    print(f"\n{with_twins}/{len(vectors_of)} problems have at least one twin (similarity >= {args.twin_min})")
    db.close()

    print(f"\nWrote {args.db} in {time.perf_counter() - start:.0f} s. Problems per type and tier:")
    for t in TYPES:
        row = [counts.get((t["key"], tier), 0) for tier in (1, 2, 3)]
        print(f"  {t['name']:10} fácil {row[0]:3}  medio {row[1]:3}  difícil {row[2]:3}")


if __name__ == "__main__":
    main()

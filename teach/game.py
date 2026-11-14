"""The rules of the game, and its SQLite storage. Standard library only.

Two modes, one hexagon:
- "solve": the student solves the problem from scratch and types the number
  (or a calculation such as "40/20 * 3"). No options shown, so no guessing.
- "correct": the student sees Qwen's step-by-step attempt, marks the step where
  it goes wrong (or "no mistake"), and gives the right final answer.

Help never gives the answer away. It's a ladder, one rung per request, and
every rung is about this problem:
  qwen_hint  a question written by Qwen ahead of time to help understand it
  sibling    the most similar solved problem of a lower level and the same topic
             (embeddings), only when it's similar enough
  attempt    Qwen's wrong attempt, to debug (solve mode only)
  options    the five options of the Kangaroo
Each rung used lowers the points. Which rungs a student used, and whether they
solved it afterwards, is stored, to rank the kinds of help by how well they work.

Stats: every type has points from 0 to 100 on the hexagon. A tier of a type is
cleared by solving SOLVED_TO_CLEAR of its problems without help (or all of
them, if it has fewer), which unlocks the next tier and gives a medal.
"""
import ast
import json
import operator
import random
import sqlite3
import time
from fractions import Fraction

from teach.kinds import TIERS, TYPE_KEYS, TYPES

SOLVED_TO_CLEAR = 2
POINTS = {1: 10, 2: 20, 3: 30}            # per problem solved without help
HELP_FACTOR = [1.0, 0.75, 0.5, 0.35, 0.25]  # by number of rungs used
# Help is only the steps of a verified worked solution (see ladder()). The other
# kinds we tried (Qwen hints, similar problem, Qwen's attempt, the options) are
# switched off: they didn't help enough. Add their names here to bring them back.
LADDERS = {"solve": [], "correct": []}
MODES = tuple(LADDERS)

SCHEMA = """
CREATE TABLE IF NOT EXISTS problems (
    id TEXT PRIMARY KEY, level INTEGER, tier INTEGER, year INTEGER,
    type TEXT, type_score REAL, text TEXT, options TEXT, label TEXT, correct_value TEXT,
    steps TEXT, qwen_answer TEXT, qwen_correct INTEGER, qwen_letter TEXT, similar TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created REAL
);
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY, user_id INTEGER, problem_id TEXT, mode TEXT,
    started REAL, finished REAL,
    hints INTEGER DEFAULT 0,      -- rungs of help used
    tries INTEGER DEFAULT 0,      -- answers sent
    step_marked INTEGER,          -- correct mode: last step marked, 0 = "no mistake"
    fix_text TEXT,                -- correct mode: the student's rewrite of that step
    answer TEXT,                  -- last answer sent, as typed
    solved INTEGER DEFAULT 0,     -- 1 right, 0 open or wrong, -1 gave up
    seconds REAL
);
CREATE TABLE IF NOT EXISTS queue (  -- each student's priorities; a missing row means 0
    user_id INTEGER, problem_id TEXT, priority REAL DEFAULT 0,
    PRIMARY KEY (user_id, problem_id)
);
"""
# Columns added after the first version: added in place, so old databases keep working
EXTRA_COLUMNS = {"problems": {"hint": "TEXT", "text_ca": "TEXT", "guide": "TEXT", "twins": "TEXT"},
                 "plays": {"game": "TEXT", "help_kinds": "TEXT DEFAULT ''", "points": "REAL DEFAULT 0"}}


def connect(path):
    db = sqlite3.connect(path, check_same_thread=False)
    db.executescript(SCHEMA)
    for table, columns in EXTRA_COLUMNS.items():
        have = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in have:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    db.commit()
    return db


# --- Exact calculator for the student's answers ------------------------------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow}


def evaluate(text):
    """'40/20*3', '2,5', '3 × 4 − 1', '2^3' -> Fraction. Raises ValueError otherwise.

    Only numbers, + - * / ^ and parentheses: never eval() on what a student types.
    """
    expr = (str(text).strip().replace("×", "*").replace("x", "*").replace("X", "*")
            .replace("÷", "/").replace("−", "-").replace("^", "**").replace(",", "."))
    if not expr or len(expr) > 80:
        raise ValueError("escribe un número o una cuenta corta")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as error:
        raise ValueError("no entiendo esa cuenta") from error

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return Fraction(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = walk(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow):
                if right.denominator != 1 or abs(right) > 12:
                    raise ValueError("solo potencias enteras pequeñas")
                return left ** int(right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise ValueError("división por cero")
            return _OPS[type(node.op)](left, right)
        raise ValueError("solo números, + - * / ^ y paréntesis")

    return walk(tree)


def show(value):
    """Fraction -> '12', '2.5' or '7/3'."""
    if value.denominator == 1:
        return str(value.numerator)
    if (value * 10000).denominator == 1:
        return f"{float(value):g}"
    return f"{value.numerator}/{value.denominator}"


# --- Users, stats and progress -------------------------------------------------

def user_id(db, name):
    name = " ".join(name.split())[:30]
    if not name:
        raise ValueError("escribe tu nombre")
    db.execute("INSERT OR IGNORE INTO users (name, created) VALUES (?, ?)", (name, time.time()))
    db.commit()
    return db.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()[0]


def available(db):
    """How many problems each type and tier has."""
    counts = {k: {1: 0, 2: 0, 3: 0} for k in TYPE_KEYS}
    for kind, tier, n in db.execute("SELECT type, tier, COUNT(*) FROM problems GROUP BY type, tier"):
        if kind in counts:
            counts[kind][tier] = n
    return counts


def stats(db, uid):
    """Per type: hexagon points (0-100), problems solved per tier, cleared tiers, current tier."""
    avail = available(db)
    rows = db.execute("""
        SELECT p.type, p.tier, MIN(pl.hints), MAX(pl.points) FROM plays pl JOIN problems p ON p.id = pl.problem_id
        WHERE pl.user_id = ? AND pl.solved = 1 GROUP BY p.id""", (uid,)).fetchall()
    out = {k: {"points": 0, "clean": {1: 0, 2: 0, 3: 0}, "helped": {1: 0, 2: 0, 3: 0}} for k in TYPE_KEYS}
    for kind, tier, hints, points in rows:
        if kind not in out:
            continue
        s = out[kind]
        s["clean" if hints == 0 else "helped"][tier] += 1
        s["points"] += points or 0
    for kind, s in out.items():
        s["points"] = min(100, round(s["points"]))
        need = {t: min(SOLVED_TO_CLEAR, avail[kind][t]) for t in (1, 2, 3)}
        s["need"] = need
        # A tier with no problems never blocks the way, but gives no medal
        s["cleared"] = [t for t in (1, 2, 3) if need[t] and s["clean"][t] >= need[t]]
        passed = [t for t in (1, 2, 3) if t in s["cleared"] or not need[t]]
        s["tier"] = next((t for t in (1, 2, 3) if t not in passed), None)  # None = mastered
    totals = db.execute("SELECT COUNT(*), SUM(solved = 1), SUM(solved = 1 AND hints = 0) FROM plays "
                        "WHERE user_id = ? AND finished IS NOT NULL", (uid,)).fetchone()
    return {"types": out, "plays": totals[0] or 0, "solved": totals[1] or 0, "clean": totals[2] or 0}


# The queue: learning without hints. A problem you fail goes to the back
# (FAIL_PRIORITY) and comes back by itself, one point per problem you start,
# so after a few others you meet it again. Its twins (same topic, almost the
# same problem, from the embeddings) jump to the front (TWIN_PRIORITY): solving
# one shows whether you learnt the idea or only that answer.
FAIL_PRIORITY = -6
TWIN_PRIORITY = 5


def _priority(db, uid, pid):
    row = db.execute("SELECT priority FROM queue WHERE user_id = ? AND problem_id = ?", (uid, pid)).fetchone()
    return row[0] if row else 0


def _set_priority(db, uid, pid, value):
    db.execute("INSERT INTO queue (user_id, problem_id, priority) VALUES (?, ?, ?) "
               "ON CONFLICT(user_id, problem_id) DO UPDATE SET priority = excluded.priority", (uid, pid, value))


def mark_failed(db, uid, pid):
    """Send a failed problem to the back of the queue and its twins to the front."""
    _set_priority(db, uid, pid, FAIL_PRIORITY)
    twins = db.execute("SELECT twins FROM problems WHERE id = ?", (pid,)).fetchone()
    for twin in json.loads(twins[0]) if twins and twins[0] else []:
        if _priority(db, uid, twin) < TWIN_PRIORITY:
            _set_priority(db, uid, twin, TWIN_PRIORITY)
    db.commit()


def pick(db, uid, kind=None, rng=random):
    """Next problem for a type (or any type), at the student's current tier.

    Never above the current tier: if it has nothing left, an easier tier, and
    if that has nothing either, None. Among the unsolved problems of a tier, the
    highest priority wins (twins of a failed problem first, failed problems
    last), then problems never seen, then chance.
    """
    s = stats(db, uid)["types"]
    kinds = [kind] if kind else [k for k in TYPE_KEYS if s[k]["tier"]]
    rng.shuffle(kinds)
    for k in kinds:
        target = s[k]["tier"] or 3
        rows = db.execute("""
            SELECT p.id, p.tier, COUNT(pl.id), MAX(pl.solved = 1), COALESCE(q.priority, 0) FROM problems p
            LEFT JOIN plays pl ON pl.problem_id = p.id AND pl.user_id = ?
            LEFT JOIN queue q ON q.problem_id = p.id AND q.user_id = ?
            WHERE p.type = ? GROUP BY p.id""", (uid, uid, k)).fetchall()
        # Unsolved problems of the current tier or easier ones; a waiting twin
        # (priority > 0) is allowed from any tier: it's almost the same problem.
        pool = [r for r in rows if not r[3] and (r[1] <= target or r[4] > 0)]
        if pool:
            # Highest priority first; then the current tier before easier ones;
            # then problems never seen; then chance. Failed ones (negative) last.
            key = lambda r: (r[4], r[1] == target, r[1], r[2] == 0)  # noqa: E731
            best = max(key(r) for r in pool)
            return rng.choice([r for r in pool if key(r) == best])[0]
    return None


def problem_public(db, pid, mode):
    """What the page may see: never the correct value or letter, and Qwen's
    attempt only in correct mode (in solve mode it's a rung of help)."""
    r = db.execute("SELECT id, level, tier, year, type, text, steps, qwen_answer, text_ca FROM problems WHERE id = ?",
                   (pid,)).fetchone()
    if not r:
        return None
    out = {"id": r[0], "level": r[1], "tier": r[2], "year": r[3], "type": r[4], "text": r[5], "text_ca": r[8]}
    if mode == "correct":
        out.update(steps=json.loads(r[6]), qwen_answer=r[7])
    return out


def ladder(db, pid, mode):
    """The rungs of help this problem has, in order.

    A verified worked solution comes first, one "guide" rung per step (never the
    last step, which gives the answer away); then the other kinds of help.
    """
    hint, similar, guide = db.execute("SELECT hint, similar, guide FROM problems WHERE id = ?", (pid,)).fetchone()
    have = {"qwen_hint": bool(hint), "sibling": bool(similar), "attempt": True, "options": True}
    steps = json.loads(guide) if guide else []
    return ["guide"] * len(steps) + [k for k in LADDERS[mode] if have[k]]


def start_play(db, uid, pid, mode):
    if mode not in MODES:
        raise ValueError("modo desconocido")
    # Every problem started brings the failed ones one step closer to coming back
    db.execute("UPDATE queue SET priority = MIN(0, priority + 1) WHERE user_id = ? AND priority < 0", (uid,))
    cur = db.execute("INSERT INTO plays (user_id, problem_id, mode, game, started) VALUES (?, ?, ?, ?, ?)",
                     (uid, pid, mode, mode, time.time()))
    db.commit()
    return cur.lastrowid


def _play(db, play_id, uid):
    r = db.execute("SELECT problem_id, hints, tries, solved, started, game, help_kinds FROM plays "
                   "WHERE id = ? AND user_id = ?", (play_id, uid)).fetchone()
    if not r:
        raise ValueError("partida no encontrada")
    return r


def help_rung(db, play_id, uid):
    """Climb one rung of the help ladder."""
    pid, hints, _tries, solved, _, mode, used = _play(db, play_id, uid)
    if solved:
        raise ValueError("esta partida ya ha terminado")
    rungs = ladder(db, pid, mode)
    if hints >= len(rungs):
        raise ValueError("ya no quedan más ayudas")
    kind = rungs[hints]
    db.execute("UPDATE plays SET hints = ?, help_kinds = ? WHERE id = ?",
               (hints + 1, ",".join(filter(None, [used, kind])), play_id))
    db.commit()
    hint, similar, options, steps, qwen_answer, guide = db.execute(
        "SELECT hint, similar, options, steps, qwen_answer, guide FROM problems WHERE id = ?", (pid,)).fetchone()
    out = {"n": hints + 1, "total": len(rungs), "kind": kind}
    if kind == "guide":
        guide = json.loads(guide)
        out.update(text=guide[hints], step=hints + 1, of=len(guide))
    elif kind == "qwen_hint":
        out["text"] = hint
    elif kind == "sibling":
        out.update(json.loads(similar))
    elif kind == "attempt":
        out.update(steps=json.loads(steps), qwen_answer=qwen_answer)
    else:
        opts = json.loads(options)
        out["options"] = [opts[k] for k in "ABCDE" if k in opts]
    return out


def check_sibling(db, play_id, uid, text):
    """The student tries the easier sibling problem. Doesn't change the main play."""
    pid = _play(db, play_id, uid)[0]
    similar = db.execute("SELECT similar FROM problems WHERE id = ?", (pid,)).fetchone()[0]
    if not similar:
        raise ValueError("este problema no tiene problema hermano")
    value = evaluate(text)
    expected = json.loads(similar)["answer"]
    try:
        right = value == evaluate(expected)
    except ValueError:
        right = show(value) == expected.strip()
    return {"right": right, "value": show(value)}


def answer(db, play_id, uid, step, text, fix_text=""):
    """Check an answer. The correct value is only told when right."""
    pid, hints, tries, solved, started, mode, used = _play(db, play_id, uid)
    if solved:
        raise ValueError("esta partida ya ha terminado")
    value = evaluate(text)
    correct_value, qwen_correct, steps, tier = db.execute(
        "SELECT correct_value, qwen_correct, steps, tier FROM problems WHERE id = ?", (pid,)).fetchone()
    right_value = value == evaluate(correct_value)
    if mode == "correct":
        if step is None:
            raise ValueError("marca primero el paso donde falla Qwen")
        step = int(step)
        if not 0 <= step <= len(json.loads(steps)):
            raise ValueError("paso fuera de rango")
        right = right_value and (not qwen_correct or step == 0)  # if Qwen was right, only "no mistake" wins
    else:
        step, right = None, right_value
    now = time.time()
    points = POINTS[tier] * HELP_FACTOR[min(hints, len(HELP_FACTOR) - 1)] if right else 0
    db.execute("UPDATE plays SET tries = ?, step_marked = ?, fix_text = ?, answer = ?, solved = ?, "
               "finished = ?, seconds = ?, points = ? WHERE id = ?",
               (tries + 1, step, str(fix_text)[:300], str(text)[:80], 1 if right else 0,
                now if right else None, round(now - started, 1) if right else None, points, play_id))
    db.commit()
    if not right and tries == 0:  # the first wrong answer counts as failing it
        mark_failed(db, uid, pid)
    out = {"right": right, "value": show(value), "tries": tries + 1, "hints": hints, "points": round(points)}
    if right:
        out["qwen_correct"] = bool(qwen_correct)
    elif right_value and qwen_correct and mode == "correct":
        out["message"] = "El número es correcto… ¿seguro que Qwen se ha equivocado?"
    return out


def give_up(db, play_id, uid):
    pid, _hints, _tries, solved, started, _mode, _used = _play(db, play_id, uid)
    if solved:
        raise ValueError("esta partida ya ha terminado")
    correct_value, qwen_correct = db.execute("SELECT correct_value, qwen_correct FROM problems WHERE id = ?",
                                             (pid,)).fetchone()
    now = time.time()
    db.execute("UPDATE plays SET solved = -1, finished = ?, seconds = ? WHERE id = ?",
               (now, round(now - started, 1), play_id))
    db.commit()
    mark_failed(db, uid, pid)
    return {"value": correct_value, "qwen_correct": bool(qwen_correct)}


def history(db, uid):
    """Every finished play in time order, with the hexagon total after it.

    The total is recomputed the way stats() computes it (best points per
    problem, each type capped at 100), so the last point equals the hexagon.
    A play that clears a tier carries the medal it won.
    """
    avail = available(db)
    need = {k: {t: min(SOLVED_TO_CLEAR, avail[k][t]) for t in (1, 2, 3)} for k in TYPE_KEYS}
    best, clean_ids = {}, set()
    clean = {k: {1: 0, 2: 0, 3: 0} for k in TYPE_KEYS}
    type_points = {k: 0.0 for k in TYPE_KEYS}
    out = []
    rows = db.execute("""
        SELECT pl.problem_id, p.type, p.tier, pl.solved, pl.hints, pl.points, pl.finished, pl.game
        FROM plays pl JOIN problems p ON p.id = pl.problem_id
        WHERE pl.user_id = ? AND pl.finished IS NOT NULL ORDER BY pl.finished""", (uid,)).fetchall()
    for pid, kind, tier, solved, hints, points, finished, mode in rows:
        if kind not in type_points:
            continue
        medal = None
        if solved == 1:
            best[pid] = max(best.get(pid, 0), points or 0)
            type_points[kind] = min(100, sum(v for q, v in best.items() if q in _type_ids(db, kind)))
            if hints == 0 and pid not in clean_ids:
                clean_ids.add(pid)
                clean[kind][tier] += 1
                if need[kind][tier] and clean[kind][tier] == need[kind][tier]:
                    medal = tier
        out.append({"t": finished, "type": kind, "tier": tier, "mode": mode, "solved": solved,
                    "hints": hints, "points": round(points or 0), "medal": medal,
                    "total": sum(round(v) for v in type_points.values())})  # same rounding as stats()
    return {"plays": out, "max": 100 * len(TYPE_KEYS)}


_TYPE_IDS = {}


def _type_ids(db, kind):
    """Problem ids of a type, cached: problems only change when prepare_app.py runs."""
    if kind not in _TYPE_IDS:
        _TYPE_IDS[kind] = {r[0] for r in db.execute("SELECT id FROM problems WHERE type = ?", (kind,))}
    return _TYPE_IDS[kind]


def help_report(db):
    """For each kind of help: how often it was the last rung before solving.

    The ranking of help kinds the pitch talks about: solved after it / used.
    """
    out = {}
    for kinds, solved in db.execute("SELECT help_kinds, solved FROM plays WHERE finished IS NOT NULL "
                                    "AND help_kinds != ''"):
        last = kinds.split(",")[-1]
        used, ok = out.get(last, (0, 0))
        out[last] = (used + 1, ok + (solved == 1))
    return {k: {"used": u, "solved_after": s, "rate": round(s / u, 2) if u else None} for k, (u, s) in out.items()}

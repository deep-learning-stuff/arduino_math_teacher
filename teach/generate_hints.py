#!/usr/bin/env python3
"""Ask Qwen for one hint per problem, ahead of time, and store it in SQLite.

    python3 teach/generate_hints.py --limit 10      # pilot
    python3 teach/generate_hints.py                 # every problem in data/teach.db

Qwen solves about 1 problem in 10, so it isn't asked how to solve the problem.
It's asked for one question that helps the student understand it: the key
condition, what is asked, or a smaller case. A grammar forces one question in
Spanish, and code rejects hints that give the answer away or do calculations.
Run it again to resume. Standard library only.
"""
import argparse
import os
import re
import sqlite3
import sys
import time
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from copilot.client import post  # noqa: E402
from copilot.config import CONFIG  # noqa: E402

URL = f"http://127.0.0.1:{CONFIG['LLM_PORT']}/v1/chat/completions"

# Stable, so llama-server only reads it once
SYSTEM = (
    "Eres un profesor de matemáticas. Un alumno está atascado en un problema de olimpiada. "
    "NO lo resuelvas y NO hagas ninguna cuenta. Señala en una pregunta corta, en español, "
    "UNA condición del enunciado que es fácil pasar por alto y que es clave para resolverlo. "
    "No repitas la pregunta del problema.\n\n"
    "Ejemplo de problema: 20 children stand in a circle. Each one holds hands with the two "
    "children next to them. How many pairs of hands are held?\n"
    "Ejemplo de pista: ¿Te has fijado en que están en círculo, así que el último también da la mano al primero?")

# One Spanish question that must start by pointing at a condition, without "="
# so it can't hold a calculation
GRAMMAR = r'''root ::= "¿Te has fijado en que " [^\n=?¿]{8,180} "?"'''

# --mode answer: Qwen is told the correct answer. It doesn't have to solve the
# problem, only to point the way towards an answer it already knows, which is
# much easier for a small model. Code then rejects hints that contain it.
SYSTEM_ANSWER = (
    "Eres un profesor de matemáticas. Un alumno está atascado en un problema de olimpiada. "
    "Tú sabes la respuesta correcta, pero el alumno NO debe saberla. Escribe UNA pista corta, "
    "en español, que le ayude a descubrir cómo llegar a esa respuesta: la idea clave, la condición "
    "que no debe olvidar o el primer paso. NUNCA escribas la respuesta ni hagas la cuenta final.\n\n"
    "Ejemplo de problema: 20 children stand in a circle. Each one holds hands with the two "
    "children next to them. How many pairs of hands are held?\n"
    "Respuesta correcta: 20\n"
    "Ejemplo de pista: Cada niño da dos manos, pero cada apretón lo cuentan dos niños a la vez. "
    "¿Cuántas veces estás contando cada apretón?")
GRAMMAR_ANSWER = r'''root ::= [^\n=]{20,240} [.?]'''

NUMBER_WORDS = {n: words.split("|") for n, words in enumerate([
    "cero", "uno|una|un", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
    "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho",
    "diecinueve", "veinte"])}

SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    id TEXT PRIMARY KEY,
    hint TEXT,
    ok INTEGER,        -- 1 passed the checks in code
    reason TEXT,       -- why it was rejected
    seconds REAL, prompt_n INTEGER, predicted_n INTEGER
)"""

NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(text):
    out = set()
    for n in NUMBER.findall(text):
        try:
            out.add(Fraction(n.replace(",", ".")))
        except ValueError:
            pass
    return out


def check(hint, text, correct_value):
    """Reject a hint that gives the answer away."""
    try:
        answer = Fraction(correct_value.replace(",", "."))
    except ValueError:
        answer = None
    if answer is not None and answer in numbers(hint) and answer not in numbers(text):
        return "contains the answer"
    if answer is not None and answer.denominator == 1 and int(answer) in NUMBER_WORDS and answer > 1:
        # 0 and 1 are skipped: "un", "una" are everywhere in Spanish
        for word in NUMBER_WORDS[int(answer)]:
            if re.search(rf"\b{word}\b", hint.lower()):
                return "contains the answer in words"
    if len(hint.split()) > 40:
        return "too long"
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/teach.db")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=90)
    ap.add_argument("--mode", choices=["notice", "answer"], default="answer",
                    help="notice: '¿Te has fijado en que...?' without the answer; answer: Qwen knows the answer")
    args = ap.parse_args()
    table = "hints" if args.mode == "notice" else "hints_answer"
    system, grammar = (SYSTEM, GRAMMAR) if args.mode == "notice" else (SYSTEM_ANSWER, GRAMMAR_ANSWER)

    db = sqlite3.connect(args.db)
    db.execute(SCHEMA.format(table=table))
    rows = db.execute(f"SELECT a.id, a.text, a.correct_value FROM attempts a LEFT JOIN {table} h ON h.id = a.id "
                      "WHERE h.id IS NULL ORDER BY a.rowid").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows)} problems to give a hint", flush=True)

    for i, (pid, text, correct_value) in enumerate(rows, 1):
        start = time.perf_counter()
        hint, reason, timings = "", "", {}
        try:
            user = text if args.mode == "notice" else f"{text}\nRespuesta correcta: {correct_value}"
            response = post(URL, {
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "grammar": grammar, "max_tokens": 90, "temperature": 0,
                "seed": int(CONFIG["SEED"]), "cache_prompt": True,
            }, timeout=args.timeout)
            hint = response["choices"][0]["message"]["content"].strip()
            timings = response.get("timings", {})
            reason = check(hint, text, correct_value)
        except Exception as error:  # noqa: BLE001
            reason = f"error: {error}"
        seconds = time.perf_counter() - start
        db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?,?,?)",
                   (pid, hint, int(not reason), reason, round(seconds, 1),
                    timings.get("prompt_n", 0), timings.get("predicted_n", 0)))
        db.commit()
        print(f"[{i}/{len(rows)}] {pid} {seconds:.0f} s {'OK' if not reason else 'REJECTED: ' + reason}\n    {hint}",
              flush=True)
    db.close()


if __name__ == "__main__":
    main()

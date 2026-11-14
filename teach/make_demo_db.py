#!/usr/bin/env python3
"""Make data/demo.db: the game with made-up problems, so the web page can be
developed on a laptop without the board, the models or the M3Kang data.

    python3 teach/make_demo_db.py
    python3 teach/server.py --db data/demo.db        # http://127.0.0.1:8000

Every problem here is written by us, in the style of the Kangaroo; none comes
from M3Kang, so this file and its output can be shared. "Qwen's attempts" are
made up too, each with one deliberate mistake, like the real ones.
"""
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from teach import game  # noqa: E402
from teach.kinds import tier_of  # noqa: E402

# (type, level, text_en, text_ca, options A-E, label, Qwen steps, Qwen answer, hint)
PROBLEMS = [
    ("calculo", 0, "Anna buys 3 notebooks at 4 euros each and pays with a 20 euro note. How much change does she get?",
     "L'Anna compra 3 llibretes a 4 euros cadascuna i paga amb un bitllet de 20 euros. Quant canvi li tornen?",
     ["6", "8", "10", "12", "16"], "B", ["3 notebooks at 4 euros cost 3 + 4 = 7 euros.", "She pays 20, so the change is 20 - 7 = 13 euros."], "13",
     "¿Cuánto cuestan de verdad 3 libretas de 4 euros?"),
    ("calculo", 3, "A train leaves at 9:40 and the trip takes 1 hour and 35 minutes. At what minute past 11 does it arrive?",
     "Un tren surt a les 9:40 i el viatge dura 1 hora i 35 minuts. A quin minut després de les 11 arriba?",
     ["5", "10", "15", "20", "25"], "C", ["9:40 plus 1 hour is 10:40.", "10:40 plus 35 minutes is 10:75.", "So it arrives at minute 75."], "75",
     "¿Cuántos minutos tiene una hora?"),
    ("algebra", 1, "Tom is 3 times as old as his sister. In 4 years the sum of their ages will be 24. How old is his sister now?",
     "En Tom té 3 vegades l'edat de la seva germana. D'aquí a 4 anys la suma de les seves edats serà 24. Quants anys té ara la germana?",
     ["3", "4", "5", "6", "8"], "B", ["Let s be the sister's age, so Tom is 3s.", "In 4 years the sum is 3s + s + 4 = 24.", "So 4s = 20 and s = 5."], "5",
     "¿Cuántos años cumplen los dos en 4 años, y no solo uno?"),
    ("algebra", 4, "Two numbers add up to 30 and one is 6 more than the other. What is the larger number?",
     "Dos nombres sumen 30 i un és 6 més gran que l'altre. Quin és el nombre més gran?",
     ["12", "15", "16", "18", "24"], "D", ["Half of 30 is 15.", "The larger is 15 + 6 = 21."], "21",
     "Si el pequeño es x, ¿cómo escribes el grande?"),
    ("geometria", 2, "A square has a perimeter of 36 cm. What is its area in square centimetres?",
     "Un quadrat té un perímetre de 36 cm. Quina és la seva àrea en centímetres quadrats?",
     ["36", "54", "72", "81", "144"], "D", ["The side of the square is 36 / 2 = 18 cm.", "The area is 18 x 18 = 324."], "324",
     "¿Cuántos lados tiene un cuadrado?"),
    ("geometria", 5, "A rectangle is 12 cm long and 5 cm wide. How long is its diagonal in centimetres?",
     "Un rectangle fa 12 cm de llarg i 5 cm d'ample. Quant fa la seva diagonal en centímetres?",
     ["11", "13", "15", "17", "60"], "B", ["The diagonal is the sum of the sides, 12 + 5 = 17."], "17",
     "¿Qué figura forman dos lados y la diagonal?"),
    ("conteo", 0, "How many different two-letter codes can you make with the letters A, B and C if a letter can't repeat?",
     "Quants codis de dues lletres diferents pots fer amb les lletres A, B i C si una lletra no es pot repetir?",
     ["3", "5", "6", "8", "9"], "C", ["There are 3 letters.", "Each code uses 2 of them, so there are 3 codes: AB, AC, BC."], "3",
     "¿Es lo mismo el código AB que el código BA?"),
    ("conteo", 4, "In a group of 6 friends, everyone shakes hands once with everyone else. How many handshakes are there?",
     "En un grup de 6 amics, tothom dona la mà una vegada a tots els altres. Quantes encaixades de mans hi ha?",
     ["12", "15", "18", "30", "36"], "B", ["Each friend shakes 5 hands.", "So there are 6 x 5 = 30 handshakes."], "30",
     "Cuando Ana da la mano a Luis, ¿cuántas veces lo has contado?"),
    ("logica", 1, "Three boxes are labelled 'apples', 'pears' and 'mixed', and every label is wrong. You may take one fruit from one box. From how many boxes must you take a fruit to know all three contents?",
     "Tres capses tenen les etiquetes 'pomes', 'peres' i 'barrejat', i totes les etiquetes són falses. Pots treure una fruita d'una capsa. De quantes capses has de treure una fruita per saber què hi ha a les tres?",
     ["0", "1", "2", "3", "4"], "B", ["You need to look in every box to be sure.", "So the answer is 3."], "3",
     "¿Qué sabes seguro de la caja que dice 'mixed'?"),
    ("logica", 6, "In a drawer there are 10 red socks and 10 blue socks. In the dark, how many socks must you take to be sure you have two of the same colour?",
     "En un calaix hi ha 10 mitjons vermells i 10 de blaus. A les fosques, quants mitjons has de treure per estar segur de tenir-ne dos del mateix color?",
     ["2", "3", "10", "11", "20"], "B", ["You could be unlucky and take all 10 red ones first.", "So you need 10 + 1 = 11."], "11",
     "¿Te piden dos del mismo color, o dos de un color concreto?"),
    ("numeros", 2, "What is the smallest number greater than 50 that is divisible by both 4 and 6?",
     "Quin és el nombre més petit més gran que 50 que és divisible per 4 i per 6 alhora?",
     ["52", "54", "60", "72", "96"], "C", ["A number divisible by 4 and 6 is divisible by 4 x 6 = 24.", "The first multiple of 24 above 50 is 72."], "72",
     "¿Es 12 divisible por 4 y por 6?"),
    ("numeros", 7, "The digits of a two-digit number add up to 9. If you swap the digits, the number grows by 27. What is the number?",
     "Les xifres d'un nombre de dues xifres sumen 9. Si les intercanvies, el nombre creix 27. Quin és el nombre?",
     ["18", "27", "36", "45", "63"], "C", ["Swapping the digits adds 27, so the digits differ by 27 / 10 = 2.7.", "Two digits that add up to 9 and differ by about 3 are 3 and 6, so the number is 63."], "63",
     "¿Cuánto cambia un número de dos cifras al intercambiarlas, en función de la diferencia de las cifras?"),
]


# Steps of a correct solution for each made-up problem, without the step that
# gives the answer: what generate_worked.py produces for the real ones.
GUIDES = {
    0: ["Each notebook costs 4 euros, so 3 notebooks cost 3 × 4 = 12 euros.",
        "She pays with a 20 euro note: the change is what is left of 20 after paying 12."],
    1: ["9:40 plus 1 hour is 10:40.", "10:40 plus 20 minutes is 11:00. How many of the 35 minutes are still left?"],
    2: ["Let s be the sister's age now, so Tom is 3s.",
        "In 4 years both are 4 years older: (3s + 4) + (s + 4) = 24, so 4s + 8 = 24."],
    3: ["Call the smaller number x, so the larger one is x + 6.", "Then x + (x + 6) = 30, so 2x = 24."],
    4: ["A square has 4 equal sides, so each side is 36 / 4 = 9 cm."],
    5: ["The diagonal and two sides form a right triangle.",
        "By Pythagoras, the diagonal squared is 12 × 12 + 5 × 5 = 169."],
    6: ["AB and BA are different codes, so the order matters.",
        "There are 3 choices for the first letter, and then 2 for the second."],
    7: ["Each friend shakes 5 hands, which gives 6 × 5 = 30.",
        "But every handshake has two people, so it was counted twice."],
    8: ["The box labelled 'mixed' can't be mixed: it has only apples or only pears.",
        "Take one fruit from that box and you know what it really holds. What do the other two labels tell you then?"],
    9: ["With 2 colours, the worst luck is one red sock and one blue sock.", "The next sock must match one of them."],
    10: ["Being divisible by 4 and by 6 means being a multiple of 12, not of 24.",
         "The multiples of 12 are 12, 24, 36, 48, ..."],
    11: ["Write the number as 10a + b; with the digits swapped it is 10b + a.",
         "The difference is 9b - 9a = 27, so b - a = 3, and also a + b = 9."],
}
TWINS = [(6, 7), (2, 3), (8, 9)]  # same topic, same idea


def main():
    path = os.path.join("data", "demo.db")
    os.makedirs("data", exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    db = game.connect(path)
    for i, (kind, level, en, ca, opts, label, steps, qwen, hint) in enumerate(PROBLEMS):
        options = dict(zip("ABCDE", opts))
        sibling = PROBLEMS[i - 1 if i % 2 else i + 1]  # the other problem of the same type
        db.execute("INSERT INTO problems (id, level, tier, year, type, type_score, text, options, label, correct_value, "
                   "steps, qwen_answer, qwen_correct, qwen_letter, similar, hint, text_ca) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                       f"demo-{i}", level, tier_of(level), 2026, kind, 1.0, en, json.dumps(options), label,
                       options[label], json.dumps(steps), qwen, 0, "", json.dumps({
                           "id": f"demo-sib-{i}", "level": sibling[1], "score": 0.8, "text": sibling[2],
                           "text_ca": sibling[3], "answer": dict(zip("ABCDE", sibling[4]))[sibling[5]]}), hint, ca))
    for i, steps in GUIDES.items():
        db.execute("UPDATE problems SET guide = ? WHERE id = ?", (json.dumps(steps), f"demo-{i}"))
    for a, b in TWINS:
        db.execute("UPDATE problems SET twins = ? WHERE id = ?", (json.dumps([f"demo-{b}"]), f"demo-{a}"))
        db.execute("UPDATE problems SET twins = ? WHERE id = ?", (json.dumps([f"demo-{a}"]), f"demo-{b}"))
    db.commit()

    # A demo student with some history, so the dashboard has something to draw
    uid = game.user_id(db, "Demo")
    rng = random.Random(7)
    for pid, helps, right in [(6, 0, 1), (7, 1, 1), (0, 0, 1), (2, 2, 1), (4, 0, 0), (1, 0, 1), (8, 1, 1), (10, 0, 1)]:
        play = game.start_play(db, uid, f"demo-{pid}", rng.choice(["solve", "correct"]))
        for _ in range(helps):
            game.help_rung(db, play, uid)
        value = db.execute("SELECT correct_value FROM problems WHERE id = ?", (f"demo-{pid}",)).fetchone()[0]
        if right:
            game.answer(db, play, uid, 0 if game._play(db, play, uid)[5] == "correct" else None, value)
        else:
            game.give_up(db, play, uid)
        time.sleep(0.01)
    db.close()
    print(f"Wrote {path}: {len(PROBLEMS)} made-up problems and a student called Demo.")
    print("Run: python3 teach/server.py --db data/demo.db")


if __name__ == "__main__":
    main()

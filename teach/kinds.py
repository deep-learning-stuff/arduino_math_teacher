"""The six problem types of the hexagon, and the difficulty tiers.

Each type has a few English descriptions (written by us, not taken from
M3Kang). prepare_app.py embeds them with bge-small and gives every problem the
type whose descriptions are closest: no hand labelling of problems.
"""

TYPES = [
    {
        "key": "calculo", "name": "Cálculo", "icon": "🔢", "color": "#ee6a12",
        "describe": [
            "Compute a result with addition, subtraction, multiplication and division.",
            "A word problem about money, prices, time, distances or speeds.",
            "Fractions, percentages and averages of quantities.",
            "How much is left after sharing, buying or spending.",
        ],
        "strategy": "Escribe cada cantidad del enunciado con su unidad y comprueba qué "
                    "operación las relaciona. ¿Qwen ha usado todos los datos, y cada uno una sola vez?",
    },
    {
        "key": "algebra", "name": "Álgebra", "icon": "⚖️", "color": "#d9336b",
        "describe": [
            "Find an unknown number from an equation or a balance.",
            "Ages of people now and in some years.",
            "Two unknown quantities with two conditions between them.",
            "A number such that some relation between quantities holds.",
        ],
        "strategy": "Llama x a lo que no sabes y traduce cada frase a una igualdad. "
                    "Comprueba si la ecuación de Qwen dice lo mismo que el enunciado.",
    },
    {
        "key": "geometria", "name": "Geometría", "icon": "📐", "color": "#1f6feb",
        "describe": [
            "Area and perimeter of squares, rectangles, triangles and circles.",
            "Angles in a figure, parallel lines and polygons.",
            "Cubes, boxes and solids built from small cubes, volume and faces.",
            "Lengths of sides and segments in a geometric figure.",
        ],
        "strategy": "Haz un dibujo con las medidas. Comprueba que cada fórmula que usa "
                    "Qwen es la de esa figura (perímetro no es área, y no todo triángulo es rectángulo).",
    },
    {
        "key": "conteo", "name": "Conteo", "icon": "🎲", "color": "#16a05c",
        "describe": [
            "In how many different ways can objects be arranged or chosen?",
            "Count the number of possible combinations, pairs or orders.",
            "How many paths, routes or possibilities are there?",
            "Count the numbers or objects that satisfy a condition.",
        ],
        "strategy": "Prueba con un caso más pequeño y cuenta a mano. ¿Qwen ha olvidado "
                    "casos, o ha contado algún caso dos veces?",
    },
    {
        "key": "logica", "name": "Lógica", "icon": "🧩", "color": "#7c4ddb",
        "describe": [
            "Some people always tell the truth and others always lie; who is who?",
            "Deduce who did what from several clues.",
            "What is the minimum number you must take to be sure that something happens?",
            "Order people or objects from several comparisons.",
        ],
        "strategy": "Busca el peor caso posible y comprueba cada pista por separado. "
                    "¿Qwen ha supuesto algo que el enunciado no dice?",
    },
    {
        "key": "numeros", "name": "Números", "icon": "✨", "color": "#f2b300",
        "describe": [
            "Digits of a number, the sum of digits and place value.",
            "Divisibility, multiples, remainders and prime numbers.",
            "Even and odd numbers and parity arguments.",
            "Find the pattern in a sequence of numbers and continue it.",
        ],
        "strategy": "Escribe los primeros casos y busca el patrón. Comprueba la regla de "
                    "Qwen con un ejemplo concreto: ¿se cumple de verdad?",
    },
]

TYPE_KEYS = [t["key"] for t in TYPES]

# M3Kang levels 0 to 7 (level 0 is grade 5) grouped into three tiers
TIERS = [
    {"tier": 1, "name": "Fácil", "levels": (0, 1, 2), "medal": "Bronce"},
    {"tier": 2, "name": "Medio", "levels": (3, 4), "medal": "Plata"},
    {"tier": 3, "name": "Difícil", "levels": (5, 6, 7), "medal": "Oro"},
]


def tier_of(level):
    for t in TIERS:
        if level in t["levels"]:
            return t["tier"]
    return TIERS[-1]["tier"]

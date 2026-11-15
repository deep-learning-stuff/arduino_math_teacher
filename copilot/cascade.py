"""The second chance: the model writes a short Python program, we run it, and
if its output is exactly one of the five options, that option is the answer.

Small models get the reasoning of a Kangaroo problem wrong far more often than
the arithmetic, and many problems (counting, digits, sums) can be solved by
trying every case, which a program does without slips. The check against the
options is free: a wrong program rarely lands on one of five given values.

solve() in pipeline.py always has a letter first (the fast answer), so this
step only runs with the time that is left, and can only replace that letter.
"""
import os
import re
import subprocess
import sys
import tempfile
import time
from fractions import Fraction

from .config import CONFIG

# --- Reading options and numbers ----------------------------------------------

DASHES = str.maketrans({"−": "-", "–": "-", "—": "-", "一": "-", "－": "-", "‒": "-"})
NUM = re.compile(r"-?\d+(?:\.\d+)?(?:/\d+)?")


def fix_option(value):
    """Undo OCR slips seen on Kangaroo images: 'O' for 0, a CJK dash for minus,
    'l' or 'I' inside a number, a space before the degree sign."""
    v = str(value).strip().lstrip(")）]").strip().translate(DASHES)
    if v in ("O", "o"):
        return "0"
    if re.fullmatch(r"-?[\dOolI]+", v) and re.search(r"\d", v):
        v = v.replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    v = re.sub(r"(\d)\s+°", r"\1°", v)
    # OCR reads "cm²" as "cm2": put the exponent back, or "36 cm2" looks like two numbers
    return re.sub(r"(\d\s*(?:mm|cm|dm|km|m))([23])\b", lambda m: m.group(1) + "²³"[int(m.group(2)) - 2], v)


def to_number(text):
    """'12', '2,5', '3/4', '115°', '9 km', '-1/3', '8:45' -> Fraction, else None.
    A time of day becomes minutes since midnight. Anything with more than one
    number, such as a list, is None."""
    s = str(text).strip().translate(DASHES)
    t = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", s.replace(" ", ""))
    if t:
        return Fraction(int(t.group(1)) * 60 + int(t.group(2)))
    if any(c in s for c in "[]()=") or ", " in s:
        return None
    s = re.sub(r"(\d),(\d)", r"\1.\2", s.replace(" ", ""))
    found = NUM.findall(s)
    if len(found) != 1:
        return None
    try:
        return Fraction(found[0])
    except (ValueError, ZeroDivisionError):
        return None


def numeric_options(options):
    """{letter: Fraction} if all five options are distinct numbers, else None."""
    if len(options) != 5:
        return None
    values = {k: to_number(v) for k, v in options.items()}
    if any(v is None for v in values.values()) or len(set(values.values())) != 5:
        return None
    return values


# --- The program --------------------------------------------------------------

SYSTEM = ("You are an expert Python programmer solving math competition problems. "
          "Write a short Python program that computes the answer by brute force or direct calculation "
          "and prints only the final number. Use only the standard library. No comments, no explanations: "
          "short code only.")

URL = f"http://127.0.0.1:{CONFIG.get('CODE_PORT', CONFIG['LLM_PORT'])}/completion"
THINK_BLOCK = CONFIG.get("CODE_TEMPLATE", "qwen3") == "qwen3"
MAX_TOKENS = int(CONFIG.get("CODE_MAX_TOKENS", "220"))
# Board speeds of the code model, in tokens per second (measured, see README)
PREFILL_TPS = float(CONFIG.get("CODE_PREFILL_TPS", "9"))
GEN_TPS = float(CONFIG.get("CODE_GEN_TPS", "5.5"))
RUN_SECONDS = 3.0  # the program itself


def prompt(problem):
    text = re.sub(r"^\s*\d+\.\s*", "", problem["text"]).strip()
    lines = [text] + [f"{k}) {problem['options'][k]}" for k in "ABCDE"]
    return (f"<|im_start|>system\n{SYSTEM}<|im_end|>\n<|im_start|>user\n" + "\n".join(lines)
            + "<|im_end|>\n<|im_start|>assistant\n" + ("<think>\n\n</think>\n\n" if THINK_BLOCK else "")
            + "```python\n")


def run_program(code, timeout):
    """Run the model's program in a separate, isolated Python, with a time limit.
    Returns its last line of output, or ''."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "C:\\Windows")
    try:
        r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                           timeout=timeout, cwd=tempfile.gettempdir(), env=env)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    lines = r.stdout.strip().splitlines()
    return lines[-1].strip() if lines else ""


def stream_until(prompt_text, n_predict, stop_at, url=None, stop=("```",)):
    """Generate with streaming and hang up at stop_at (a perf_counter value).

    Hanging up matters: llama-server notices the closed connection and stops
    generating. A plain request that times out on our side would keep the
    server busy, and the next problem would wait behind it.
    Returns (text, finished)."""
    import http.client
    import json
    from urllib.parse import urlparse
    u = urlparse(url or URL)
    left = stop_at - time.perf_counter()
    if left <= 0:
        return "", False
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=left)
    text, finished = [], False
    try:
        conn.request("POST", u.path, json.dumps({
            "prompt": prompt_text, "n_predict": n_predict, "stop": list(stop), "temperature": 0,
            "seed": int(CONFIG["SEED"]), "cache_prompt": True, "stream": True}),
            {"Content-Type": "application/json"})
        resp = conn.getresponse()
        while True:
            left = stop_at - time.perf_counter()
            if left <= 0:
                break
            conn.sock.settimeout(left)
            line = resp.readline()
            if not line:
                break
            line = line.strip()
            if not line.startswith(b"data: "):
                continue
            chunk = json.loads(line[6:])
            text.append(chunk.get("content", ""))
            if chunk.get("stop"):
                finished = True
                break
    except (OSError, ValueError):
        pass
    finally:
        conn.close()
    return "".join(text), finished


TRANSLATE_URL = f"http://127.0.0.1:{CONFIG['LLM_PORT']}/completion"


def translate(text, stop_at):
    """English version of a Catalan statement, by the small model, or '' if it
    did not finish before stop_at. The options are numbers, so they stay as they are."""
    text = re.sub(r"^\s*\d+\.\s*", "", text).strip()
    # Tried on the board's model: a system prompt made it echo the Catalan or
    # start solving; the instruction inside the user turn translates cleanly.
    p = (f"<|im_start|>user\nTranslate from Catalan to English:\n{text}<|im_end|>\n"
         "<|im_start|>assistant\n<think>\n\n</think>\n\n")
    n = int(len(text) / 3.0) + 20  # English is about as long as Catalan
    out, finished = stream_until(p, n, stop_at, url=TRANSLATE_URL, stop=("<|im_end|>", "\n\n\n"))
    return out.strip() if finished and len(out.strip()) > len(text) * 0.5 else ""


def code_letter(problem, deadline, measured_prefill_tps=None):
    """Letter whose value the program printed, or None. Never runs past deadline
    (a time.perf_counter() value).

    measured_prefill_tps: the reading speed the letter request just showed. When
    the code model is the same server, it tells how busy the board is right now,
    and both speeds are scaled by it (never above the configured ones)."""
    values = numeric_options(problem.get("options") or {})
    if values is None:
        return None, {"why": "options are not five numbers"}
    scale = 1.0
    if measured_prefill_tps and CONFIG.get("CODE_PORT", CONFIG["LLM_PORT"]) == CONFIG["LLM_PORT"]:
        scale = min(1.0, measured_prefill_tps / PREFILL_TPS)
    p = prompt(problem)
    # The system prompt is the same every time, so the server keeps it cached:
    # only the problem itself is read (about 4 characters per token).
    new_chars = len(p) - len(SYSTEM)
    prefill = new_chars / 4.0 / (PREFILL_TPS * scale)
    left = deadline - time.perf_counter() - RUN_SECONDS - prefill - 1.0
    n = int(min(MAX_TOKENS, left * GEN_TPS * scale * 0.9))
    if n < 30:  # the coder's programs are often 10 to 60 tokens
        return None, {"why": "no time"}
    code, finished = stream_until(p, n, deadline - RUN_SECONDS)
    if not finished:
        return None, {"why": "out of time", "tokens": n}
    out = run_program(code, min(RUN_SECONDS, max(0.5, deadline - time.perf_counter() - 0.5)))
    value = to_number(out)
    hits = [k for k, v in values.items() if value is not None and v == value]
    return (hits[0] if len(hits) == 1 else None), {"printed": out[:40], "tokens": n}


# --- Checking the options instead of solving -----------------------------------
#
# A small model often cannot build the answer, but can say whether a given
# number fits the conditions ("which number ... such that ..."). So the code
# model writes ok(x), and we try it on the five options. One True is an
# answer; two to four True rule the others out, and the vote picks among them.
#
# The prompt starts exactly like the program's, so when the program ran just
# before, the server has all of it cached and only reads the function header.

CHECK_HEAD = ("def ok(x):\n"
              "    \"\"\"Return True if x is the correct answer to the problem, False otherwise.\"\"\"\n")
CHECK_TOKENS = int(CONFIG.get("CHECK_MAX_TOKENS", "160"))


def function_body(body):
    """Only the indented lines of ok(x). Measured on the board: the model often
    goes on after the function with its own loop or prints, sometimes with
    characters that are not valid Python, and that broke the whole checker."""
    kept = []
    for line in body.splitlines():
        if line.strip() and not line[:1].isspace():
            break
        kept.append(line)
    return "\n".join(kept).rstrip()


def checker_source(body, values):
    """The program we run: the model's ok(x), then a loop over the options.
    Integers are passed as int, the rest as Fraction, so x == 3 and x / 2 work."""
    body = function_body(body)
    literals = []
    for v in values:
        literals.append(str(v.numerator) if v.denominator == 1 else f"Fraction({v.numerator}, {v.denominator})")
    return ("from fractions import Fraction\n" + CHECK_HEAD + body + "\n\n"
            "_r = []\n"
            f"for _i, _x in enumerate([{', '.join(literals)}]):\n"
            "    try:\n"
            "        _r.append(str(_i) if ok(_x) is True else '')\n"
            "    except Exception:\n"
            "        _r.append('')\n"
            "print('OK:' + ','.join(c for c in _r if c))\n")


def check_letters(problem, deadline, measured_prefill_tps=None, cached=True):
    """(letters for which ok(x) is True, info), or (None, info) if no usable
    checker came back in time. Never runs past deadline (a perf_counter value).
    cached: whether the program prompt for this problem was just sent."""
    values = numeric_options(problem.get("options") or {})
    if values is None:
        return None, {"why": "options are not five numbers"}
    scale = 1.0
    if measured_prefill_tps and CONFIG.get("CODE_PORT", CONFIG["LLM_PORT"]) == CONFIG["LLM_PORT"]:
        scale = min(1.0, measured_prefill_tps / PREFILL_TPS)
    base = prompt(problem)
    p = base + CHECK_HEAD
    new_chars = len(CHECK_HEAD) if cached else len(p) - len(SYSTEM)
    prefill = new_chars / 4.0 / (PREFILL_TPS * scale)
    left = deadline - time.perf_counter() - RUN_SECONDS - prefill - 1.0
    n = int(min(CHECK_TOKENS, left * GEN_TPS * scale * 0.9))
    if n < 25:
        return None, {"why": "no time"}
    body, finished = stream_until(p, n, deadline - RUN_SECONDS)
    if not finished or "return" not in body:
        return None, {"why": "out of time" if not finished else "no return", "tokens": n}
    letters = sorted(values)
    src = checker_source(body.rstrip(), [values[k] for k in letters])
    out = run_program(src, min(RUN_SECONDS, max(0.5, deadline - time.perf_counter() - 0.5)))
    if not out.startswith("OK:"):
        return None, {"why": "checker crashed", "tokens": n}
    passed = [letters[int(i)] for i in out[3:].split(",") if i]
    if not passed or len(passed) == 5:  # says nothing
        return None, {"why": f"{len(passed)} options pass", "tokens": n}
    return passed, {"passed": "".join(passed), "tokens": n}

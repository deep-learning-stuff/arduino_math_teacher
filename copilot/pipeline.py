"""The baseline copilot: one problem in, one letter out.

This is the file to change first. predict.py and eval.py both call solve(),
so whatever you build here is exactly what the official run measures.
"""
import socket
import time
import urllib.error

from . import memory as memory_mod
from . import priors
from .cascade import check_letters, code_letter, fix_option, translate
from .config import CONFIG
from .llm import URL, answer_letter

TIME_LIMIT = float(CONFIG["TIME_LIMIT_SECONDS"])
# "letter": one fast letter. "cascade": the letter, then a program with the time
# left. "code_first": a program first (Catalan translated to English first if
# TRANSLATE_CAT=1), then the letter only if the program found no option.
STRATEGY = CONFIG.get("STRATEGY", "letter")
LETTER_RESERVE = float(CONFIG.get("LETTER_RESERVE_SECONDS", "14"))
TRANSLATE_CAT = CONFIG.get("TRANSLATE_CAT", "0") == "1"


def code_first(problem, deadline):
    info = {}
    if TRANSLATE_CAT and problem.get("language") == "cat":
        english = translate(problem["text"], deadline - 20)  # leave the program 20 s at least
        info["translated"] = bool(english)
        if english:
            problem = dict(problem, text=english)
    coded, more = code_letter(problem, deadline)
    info.update(more)
    return coded, info
FALLBACK = CONFIG["FALLBACK_ANSWER"]
MARGIN = 3.0  # seconds kept free for Python and for writing the answer

# --- "combo": memory, prior x model vote, program, option checker --------------
#
# Each step adds evidence, from the cheapest and surest to the slowest:
#   1. memory   a solved copy of the problem whose answer is among the options
#   2. vote     the letter model's probabilities times the prior (option rank
#               for numbers, letter frequency otherwise). Always available.
#   3. program  the code model computes the answer: if it is an option, it wins
#   4. checker  the code model writes ok(x): the options that fail are ruled
#               out, and the vote picks among the rest
# Anything that fails or runs out of time leaves the answer of the step before.
PRIOR_WEIGHT = float(CONFIG.get("PRIOR_WEIGHT", "0.5"))
# 0: skip the program and give the checker all the time left (an ablation)
USE_PROGRAM = CONFIG.get("USE_PROGRAM", "1") == "1"
# Off: measured on the board, 10 problems, checker alone: it decided 6 and got
# all 6 wrong (the model writes "return x == <its guess>", it does not check).
USE_CHECKER = CONFIG.get("USE_CHECKER", "0") == "1"
_PRIOR = None
_MEMORY = None


def _prior():
    global _PRIOR
    if _PRIOR is None:
        _PRIOR = priors.load()
    return _PRIOR


def _memory():
    global _MEMORY
    if _MEMORY is None:
        _MEMORY = memory_mod.load() if CONFIG.get("USE_MEMORY", "1") == "1" else memory_mod.Memory([])
    return _MEMORY


def combo(problem, result, start, time_limit, exclude_id=None, llm_url=URL, examples=()):
    deadline = start + time_limit - MARGIN
    prior = _prior()(problem)
    result["answer"], result["source"] = priors.best(prior), "prior"

    remembered, info = _memory().lookup(problem, exclude_id=exclude_id)
    result["memory"] = info
    if remembered:
        result["answer"], result["source"], result["status"] = remembered, "memory", "ok:memory"
        return result

    letter, timings = answer_letter(problem, examples, timeout=max(deadline - time.perf_counter(), 1.0),
                                    url=llm_url, with_probs=True)
    result["prompt_tokens"] = timings.get("prompt_n", 0)
    probs = timings.get("probs") or {}
    result["llm_letter"] = letter
    result["probs"] = probs
    vote = priors.combine(probs, prior, PRIOR_WEIGHT) if probs else None
    if vote:
        result["answer"], result["source"] = priors.best(vote), "vote"
    else:  # no probabilities from this server: the model's letter
        result["answer"], result["source"] = letter, "letter"
        vote = prior

    tps = timings.get("prompt_per_second") if timings.get("prompt_n", 0) >= 30 else None
    try:
        sent = False
        if USE_PROGRAM:
            coded, info = code_letter(problem, deadline=deadline, measured_prefill_tps=tps)
            result["code"] = info
            if coded:
                result["answer"], result["source"], result["status"] = coded, "code", "ok:code"
                return result
            sent = "tokens" in info  # the program prompt is now in the server's cache
        if not USE_CHECKER:
            return result
        passed, info = check_letters(problem, deadline=deadline, measured_prefill_tps=tps, cached=sent)
        result["check"] = info
        if passed:
            result["answer"] = priors.best(vote, passed)
            result["source"] = "check" if len(passed) == 1 else f"check+vote:{''.join(passed)}"
            result["status"] = "ok:check"
    except Exception as error:  # noqa: BLE001 - the vote stands
        result.setdefault("code", {})["error"] = type(error).__name__
    return result


def solve(problem, index=None, k=0, exclude_id=None, image_b64=None, llm_url=URL,
          time_limit=TIME_LIMIT):
    """Return {"answer", "seconds", "status", ...} for one problem.

    index and k turn on retrieval: the k most similar solved problems are
    added to the prompt as examples. Each example costs reading time.
    image_b64 and llm_url send the problem image to a vision server.
    """
    start = time.perf_counter()
    result = {"answer": FALLBACK, "status": "ok", "prompt_tokens": 0, "retrieved": "", "examples": []}
    try:
        examples = []
        if index is not None and k > 0:
            hits = index.search(problem, k=k, exclude_id=exclude_id)
            examples = [p for _score, p in hits]
            result["examples"] = hits
            result["retrieved"] = ";".join(f"{p['id']}:{s:.2f}" for s, p in hits)
        if problem.get("options"):  # undo OCR slips ("O" for 0, odd dashes) before anything reads them
            problem = dict(problem, options={k: fix_option(v) for k, v in problem["options"].items()})
        if STRATEGY == "combo" and image_b64 is None:
            combo(problem, result, start, time_limit, exclude_id=exclude_id, llm_url=llm_url, examples=examples)
            result["seconds"] = round(time.perf_counter() - start, 2)
            return result
        if STRATEGY == "code_first" and image_b64 is None and not examples:
            # The program first, with most of the time; the fast letter only
            # if the program found nothing and there is still time for it.
            deadline = start + time_limit - MARGIN
            try:
                coded, info = code_first(problem, deadline - LETTER_RESERVE)
                result["code"] = info
                if coded:
                    result["answer"], result["status"] = coded, "ok:code"
                    result["seconds"] = round(time.perf_counter() - start, 2)
                    return result
            except Exception as error:  # noqa: BLE001 - fall back to the letter
                result["code"] = {"why": f"{type(error).__name__}"}
            if deadline - time.perf_counter() < LETTER_RESERVE * 0.8:
                result["status"] = "ok:fallback"
                result["seconds"] = round(time.perf_counter() - start, 2)
                return result
        remaining = time_limit - (time.perf_counter() - start) - MARGIN
        letter, timings = answer_letter(problem, examples, timeout=max(remaining, 1.0),
                                        image_b64=image_b64, url=llm_url)
        result["answer"] = letter
        result["prompt_tokens"] = timings.get("prompt_n", 0)
        if STRATEGY == "cascade" and image_b64 is None:
            # A letter is safe in hand; with the time left, try a program.
            # Any failure here keeps the letter.
            try:
                coded, info = code_letter(problem, deadline=start + time_limit - MARGIN,
                                          measured_prefill_tps=timings.get("prompt_per_second")
                                          if timings.get("prompt_n", 0) >= 30 else None)
                result["code"] = info
                if coded:
                    result["answer"], result["status"] = coded, "ok:code"
            except Exception as error:  # noqa: BLE001 - the fast letter stands
                result["code"] = {"why": f"{type(error).__name__}"}
    except (socket.timeout, TimeoutError):
        result["status"] = "timeout"
    except urllib.error.URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            result["status"] = "timeout"
        else:
            result["status"] = f"error: {error.reason}"
    result["seconds"] = round(time.perf_counter() - start, 2)
    return result

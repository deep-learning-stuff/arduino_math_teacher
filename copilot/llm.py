"""Ask the small language model for one letter, A to E."""
import math
import urllib.error

from .config import CONFIG
from .client import post

URL = f"http://127.0.0.1:{CONFIG['LLM_PORT']}/v1/chat/completions"

# Keep the system prompt stable: llama-server caches the start of the prompt,
# so text that never changes is only read once.
SYSTEM = ("You solve multiple-choice math competition problems for school students. "
          "Read the problem and the options, then reply with the letter of the correct option.")


def format_problem(problem, sees_image=False):
    lines = [problem["text"].strip()]
    options = problem.get("options") or {}
    for letter in "ABCDE":
        if letter in options:
            lines.append(f"{letter}) {options[letter]}")
    if not options:
        lines.append("(The options are in the picture.)" if sees_image else
                     "(The options are in a picture that you can't see. Pick the most likely letter.)")
    return "\n".join(lines)


def build_messages(problem, examples=(), image_b64=None):
    """image_b64 is a PNG as base64 text. Only a server started with --mmproj can see it."""
    messages = [{"role": "system", "content": SYSTEM}]
    for ex in examples:  # solved problems retrieved with RAG, if any
        messages.append({"role": "user", "content": format_problem(ex) + "\nAnswer:"})
        messages.append({"role": "assistant", "content": ex["label"]})
    text = format_problem(problem, sees_image=bool(image_b64)) + "\nAnswer:"
    if image_b64:
        content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + image_b64}},
                   {"type": "text", "text": text}]
    else:
        content = text
    messages.append({"role": "user", "content": content})
    return messages


def letter_probs(response):
    """{letter: probability} from the top log-probabilities of the one token
    the model wrote, or {} if the server sent none. llama-server reports them
    from the raw logits, before the A-E grammar, so tokens such as " B" or
    "b" appear too: they are added to their letter, and the rest is dropped."""
    try:
        top = response["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    except (KeyError, IndexError, TypeError):
        return {}
    probs = {}
    for item in top:
        letter = str(item.get("token", "")).strip().strip(")").upper()
        if letter in ("A", "B", "C", "D", "E"):
            probs[letter] = probs.get(letter, 0.0) + math.exp(float(item.get("logprob", -99)))
    total = sum(probs.values())
    return {k: v / total for k, v in probs.items()} if total > 0 else {}


def answer_letter(problem, examples=(), timeout=60, image_b64=None, url=URL, with_probs=False):
    """Return (letter, timings). The grammar makes any other output impossible.
    With with_probs, timings also has "probs": {letter: probability}."""
    payload = {
        "messages": build_messages(problem, examples, image_b64),
        "grammar": "root ::= [A-E]",
        "max_tokens": 1,
        "temperature": 0,
        "seed": int(CONFIG["SEED"]),
        "cache_prompt": True,
    }
    if with_probs:
        try:
            response = post(url, dict(payload, logprobs=True, top_logprobs=20), timeout=timeout)
        except urllib.error.HTTPError:
            # A server that refuses log-probabilities must not cost us the letter.
            # The prompt is cached by now, so the retry is fast.
            response = post(url, payload, timeout=timeout)
    else:
        response = post(url, payload, timeout=timeout)
    letter = response["choices"][0]["message"]["content"].strip()
    timings = dict(response.get("timings", {}))
    if with_probs:
        timings["probs"] = letter_probs(response)
    return letter, timings

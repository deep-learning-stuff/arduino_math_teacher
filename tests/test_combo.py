"""Tests for the "combo" strategy, with the models replaced by fakes.

    python3 -m unittest tests.test_combo -v

They check the decision logic only (which step answers, what happens when a
step fails or runs out of time), not accuracy: that needs eval.py on the board.
"""
import socket
import time
import unittest
from fractions import Fraction

from copilot import cascade, llm, memory, pipeline, priors

NUM = {"A": "10", "B": "12", "C": "13", "D": "14", "E": "15"}
TEXT = "Joan has built a cube using 27 small black and white cubes. How many small white cubes has Joan used?"


def problem(options=NUM, text=TEXT, label="C", pid="p1", level=3):
    return {"id": pid, "language": "eng", "level": level, "text": text, "options": dict(options), "label": label}


class Fakes:
    """Replace the letter model and the code model for one test."""

    def __init__(self, letter="A", probs=None, program=None, checker=None, letter_error=None):
        self.letter, self.probs, self.program, self.checker = letter, probs, program, checker
        self.letter_error = letter_error
        self.calls = []

    def answer_letter(self, prob, examples=(), timeout=60, url=None, with_probs=False, image_b64=None):
        self.calls.append("letter")
        if self.letter_error:
            raise self.letter_error
        return self.letter, {"prompt_n": 100, "prompt_per_second": 11,
                             "probs": dict(self.probs or {}) if with_probs else {}}

    def stream_until(self, prompt_text, n, stop_at, url=None, stop=None):
        if prompt_text.endswith(cascade.CHECK_HEAD):
            self.calls.append("check")
            return (self.checker, True) if self.checker is not None else ("", False)
        self.calls.append("program")
        return (self.program, True) if self.program is not None else ("", False)

    def __enter__(self):
        self.saved = (pipeline.answer_letter, cascade.stream_until, pipeline.STRATEGY, pipeline._MEMORY)
        pipeline.answer_letter = self.answer_letter
        cascade.stream_until = self.stream_until
        pipeline.STRATEGY = "combo"
        self.saved_checker = pipeline.USE_CHECKER
        pipeline.USE_CHECKER = True
        pipeline._MEMORY = memory.Memory([])
        return self

    def __exit__(self, *exc):
        pipeline.answer_letter, cascade.stream_until, pipeline.STRATEGY, pipeline._MEMORY = self.saved
        pipeline.USE_CHECKER = self.saved_checker


class PriorTest(unittest.TestCase):
    def setUp(self):
        self.prior = priors.load()

    def test_numbers_favour_the_middle(self):
        dist = self.prior(problem())
        self.assertAlmostEqual(sum(dist.values()), 1.0)
        self.assertEqual(priors.best(dist), "C")  # 13 is the middle of 10..15
        self.assertLess(dist["A"], dist["C"])

    def test_rank_not_letter(self):
        shuffled = {"A": "13", "B": "15", "C": "10", "D": "12", "E": "14"}
        self.assertEqual(priors.best(self.prior(problem(shuffled))), "A")

    def test_picture_options_use_letter_frequency(self):
        self.assertEqual(priors.best(self.prior(problem({}))), "D")

    def test_combine(self):
        prior = {"A": .1, "B": .1, "C": .6, "D": .1, "E": .1}
        llm_probs = {"A": .4, "B": .3, "C": .1, "D": .1, "E": .1}
        self.assertEqual(priors.best(priors.combine(llm_probs, prior, 0)), "A")  # model alone
        self.assertEqual(priors.best(priors.combine(llm_probs, prior, 2)), "C")  # prior wins
        self.assertEqual(priors.best(priors.combine({}, prior, 0.5)), "C")       # no model
        self.assertEqual(priors.best(prior, "AB"), "A")                          # restricted


class LetterProbsTest(unittest.TestCase):
    def test_variants_are_added(self):
        response = {"choices": [{"logprobs": {"content": [{"top_logprobs": [
            {"token": "B", "logprob": -0.5}, {"token": " b", "logprob": -2.0},
            {"token": "A", "logprob": -1.0}, {"token": "The", "logprob": -0.1}]}]}}]}
        probs = llm.letter_probs(response)
        self.assertAlmostEqual(sum(probs.values()), 1.0)
        self.assertEqual(max(probs, key=probs.get), "B")
        self.assertNotIn("The", probs)

    def test_missing(self):
        self.assertEqual(llm.letter_probs({"choices": [{}]}), {})


class MemoryTest(unittest.TestCase):
    def setUp(self):
        stored = problem({"A": "13", "B": "10", "C": "12", "D": "14", "E": "15"}, label="A", pid="old")
        self.memory = memory.Memory([stored])

    def test_matches_by_value_not_letter(self):
        letter, info = self.memory.lookup(problem())
        self.assertEqual(letter, "C")  # 13 is C today, A in the stored copy
        self.assertGreaterEqual(info["similarity"], 0.9)

    def test_value_not_among_options(self):
        self.assertIsNone(self.memory.lookup(problem({"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"}))[0])

    def test_pictures_are_not_trusted(self):
        mem = memory.Memory([problem({}, label="D", pid="old")])
        self.assertIsNone(mem.lookup(problem({}))[0])

    def test_exclude_itself_and_different_text(self):
        self.assertIsNone(self.memory.lookup(problem(), exclude_id="old")[0])
        other = "A train leaves the station at noon and travels at sixty kilometres per hour towards the coast."
        self.assertIsNone(self.memory.lookup(problem(text=other))[0])


class CheckerTest(unittest.TestCase):
    VALUES = [Fraction(7), Fraction(14), Fraction(1, 2), Fraction(21), Fraction(5)]

    def run_body(self, body, timeout=3):
        return cascade.run_program(cascade.checker_source(body, self.VALUES), timeout)

    def test_passes(self):
        self.assertEqual(self.run_body("    return x % 7 == 0 and x > 10"), "OK:1,3")

    def test_fraction_and_crash(self):
        self.assertEqual(self.run_body("    return x == Fraction(1, 2)"), "OK:2")
        self.assertEqual(self.run_body("    return 1 / 0"), "OK:")

    def test_infinite_loop_is_cut(self):
        start = time.perf_counter()
        self.assertEqual(self.run_body("    while True:\n        pass", timeout=1), "")
        self.assertLess(time.perf_counter() - start, 3)


class ComboFlowTest(unittest.TestCase):
    PROBS = {"A": .5, "B": .2, "C": .1, "D": .1, "E": .1}

    def solve(self, prob=None, **kw):
        return pipeline.solve(prob or problem(), time_limit=kw.pop("time_limit", 60))

    def test_memory_answers_without_models(self):
        with Fakes() as f:
            pipeline._MEMORY = memory.Memory([problem({"A": "13", "B": "10", "C": "12", "D": "14", "E": "15"},
                                                      label="A", pid="old")])
            r = self.solve()
        self.assertEqual((r["answer"], r["source"]), ("C", "memory"))
        self.assertEqual(f.calls, [])

    def test_program_wins(self):
        with Fakes(letter="A", probs=self.PROBS, program="print(14)") as f:
            r = self.solve()
        self.assertEqual((r["answer"], r["source"]), ("D", "code"))
        self.assertEqual(f.calls, ["letter", "program"])

    def test_checker_with_one_option(self):
        with Fakes(probs=self.PROBS, program="print(99)", checker="    return x == 15") as f:
            r = self.solve()
        self.assertEqual((r["answer"], r["source"]), ("E", "check"))
        self.assertEqual(f.calls, ["letter", "program", "check"])

    def test_checker_with_several_lets_the_vote_choose(self):
        with Fakes(probs={"A": .05, "B": .4, "C": .05, "D": .3, "E": .2}, program="print(99)",
                   checker="    return x in (12, 14)"):
            r = self.solve()
        self.assertEqual(r["answer"], "B")
        self.assertTrue(r["source"].startswith("check+vote"))

    def test_checker_that_passes_everything_says_nothing(self):
        with Fakes(probs=self.PROBS, program="print(99)", checker="    return True"):
            r = self.solve()
        self.assertEqual(r["source"], "vote")

    def test_letter_timeout_keeps_the_prior(self):
        with Fakes(letter_error=socket.timeout()):
            r = self.solve()
        self.assertEqual((r["answer"], r["status"]), ("C", "timeout"))

    def test_no_probs_keeps_the_model_letter(self):
        with Fakes(letter="B", probs={}, program=None):
            r = self.solve()
        self.assertEqual((r["answer"], r["source"]), ("B", "letter"))

    def test_text_options_skip_the_code_model(self):
        opts = {"A": "red", "B": "blue", "C": "green", "D": "black", "E": "white"}
        with Fakes(letter="B", probs=self.PROBS) as f:
            r = self.solve(problem(opts))
        self.assertEqual(f.calls, ["letter"])
        self.assertIn(r["answer"], "ABCDE")

    def test_no_time_for_code(self):
        with Fakes(probs=self.PROBS, program="print(14)") as f:
            r = self.solve(time_limit=6)
        self.assertEqual(f.calls, ["letter"])
        self.assertLess(r["seconds"], 6)


if __name__ == "__main__":
    unittest.main()

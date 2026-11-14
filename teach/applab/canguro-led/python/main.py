# Canguro Coach on the LED matrix.
#
# The game runs in teach/server.py, outside App Lab. Every event a student
# causes (a new problem, a step marked, a help, a right or wrong answer) is
# published at GET /api/led. This app polls it and draws one 8x13 frame per
# event. Nothing flashes: each frame stays until the next event.
import json
import os
import time
import urllib.request

import numpy as np
from arduino.app_utils import App, Bridge, Frame

# The game server listens on the board itself. If App Lab runs this Python in a
# container that can't see the board's 127.0.0.1, set CANGURO_URL.
URLS = [os.environ.get("CANGURO_URL", ""), "http://127.0.0.1:8000/api/led", "http://172.17.0.1:8000/api/led"]

GLYPHS = {
    "0": [".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."],
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
    "3": ["####.", "....#", "....#", ".###.", "....#", "....#", "####."],
    "4": ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."],
    "5": ["#####", "#....", "####.", "....#", "....#", "#...#", ".###."],
    "6": [".###.", "#....", "#....", "####.", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
    "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", ".####", "....#", "....#", ".###."],
    "?": [".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "-": [".....", ".....", ".....", "#####", ".....", ".....", "....."],
}

# Full-matrix pictures, 8 rows by 13 columns
PICTURES = {
    "right": [
        ".............",
        "...........#.",
        "..........##.",
        ".........##..",
        ".#......##...",
        ".##....##....",
        "..##..##.....",
        "...####......",
    ],
    "wrong": [
        "..#.......#..",
        "...#.....#...",
        "....#...#....",
        ".....#.#.....",
        "......#......",
        ".....#.#.....",
        "....#...#....",
        "...#.....#...",
    ],
    "logo": [
        "......#......",
        "....##.##....",
        "..##.....##..",
        "..#...#...#..",
        "..#..###..#..",
        "..##.....##..",
        "....##.##....",
        "......#......",
    ],
}


def blank():
    return np.zeros((8, 13), dtype=np.uint8)


def picture(name, level=7):
    grid = blank()
    for r, row in enumerate(PICTURES[name]):
        for c, ch in enumerate(row):
            if ch == "#":
                grid[r, c] = level
    return grid


def text(chars, level=7):
    """One character centred, or two side by side with one column between them."""
    grid = blank()
    starts = [4] if len(chars) == 1 else [1, 7]
    for ch, start in zip(chars, starts):
        for r, row in enumerate(GLYPHS.get(ch, GLYPHS["?"])):
            for c, px in enumerate(row):
                if px == "#":
                    grid[r, start + c] = level
    return grid


def frame_for(event):
    kind = event.get("event")
    if kind == "right":
        return picture("right")
    if kind == "wrong":
        return picture("wrong")
    if kind == "giveup":
        return text("-")
    if kind == "step":
        step = int(event.get("step", 0))
        return picture("right", 3) if step == 0 else text(str(step)[-1])
    if kind == "hint":
        return text("P" + str(event.get("n", 1))[-1])
    if kind == "problem":
        return text("?")
    return picture("logo", 4)


def show(grid):
    Bridge.call("draw", Frame(grid).to_board_bytes())


state = {"seq": None, "url": None}


def fetch():
    for url in ([state["url"]] if state["url"] else []) + [u for u in URLS if u]:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                event = json.loads(response.read())
            if state["url"] != url:
                print(f"Reading game events from {url}", flush=True)
            state["url"] = url
            return event
        except (OSError, ValueError):
            continue
    return None


def loop():
    event = fetch()
    if event is None:
        if state["seq"] != "offline":
            state["seq"] = "offline"
            show(picture("logo", 2))  # dim logo: the game server isn't running
    elif event.get("seq") != state["seq"]:
        state["seq"] = event.get("seq")
        show(frame_for(event))
    time.sleep(0.3)


show(picture("logo", 4))
App.run(user_loop=loop)

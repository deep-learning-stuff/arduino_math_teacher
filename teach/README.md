# Canguro Coach: the teaching app

A web page served by the Arduino UNO Q where students train with Kangaroo
problems from M3Kang, in two modes:

- **Resuélvelo tú**: solve a problem from scratch and type the number (or a
  calculation such as `40/20*3`). No options are shown, so guessing doesn't work.
  If stuck, a ladder of help unlocks one rung at a time and never gives the answer.
- **Corrige a Qwen**: the small language model on the board tried the problem
  step by step and (almost always) got it wrong. Find the step where it fails
  and give the right answer.

Progress is a hexagon of six topics (Cálculo, Álgebra, Geometría, Conteo,
Lógica, Números), three difficulty tiers per topic with bronze, silver and gold
medals, and a chart of how the total grows over time.

Nothing calls a model while a student plays: Qwen's attempts, the topics and
the similar problems are prepared ahead of time, on the board, and stored in
SQLite. The web server only reads and writes SQLite, so every click answers in
milliseconds.

## Work on the page without the board

The page is one file, [`static/index.html`](static/index.html): HTML, CSS and
JavaScript, no build step, no libraries, nothing loaded from the internet (it
must work offline at the demo).

```bash
python3 teach/make_demo_db.py                  # data/demo.db, made-up problems only
python3 teach/server.py --db data/demo.db      # then open http://127.0.0.1:8000
```

On Windows use `python -X utf8` instead of `python3`. Log in as **Demo** to see
a student with history, or with any other name to start from zero. Edit
`static/index.html` and reload the browser: the server reads the file on every
request.

## Run it on the board, with the real problems

From the repository folder on the board, with the services from the main
README running:

```bash
python3 teach/generate_solutions.py          # Qwen's attempts -> data/teach.db (hours; resumable)
python3 teach/generate_worked.py             # verified worked solutions for the hints (hours; resumable)
python3 teach/prepare_app.py --require-guide # types, tiers, hints, similar problems -> data/app.db
python3 teach/server.py                      # http://127.0.0.1:8000 on the board
```

Open it from the laptop with `ssh -L 8000:127.0.0.1:8000 arduino@<board-ip>`, or
over USB with `adb forward tcp:8000 tcp:8000`, then <http://127.0.0.1:8000>.

Catalan statements come from M3Kang too: run
`python laptop/prepare_m3kang.py --languages cat --no-ocr --out data/cat` on the
laptop and copy `data/cat/` to the board before `prepare_app.py`.

## Files

| File | What it does |
| --- | --- |
| `static/index.html` | The whole web page |
| `server.py` | Standard-library web server and JSON API |
| `game.py` | The rules: exact calculator for answers, help ladder, points, medals, history. SQLite schema |
| `kinds.py` | The six topics (name, colour, descriptions for the embeddings) and the three tiers |
| `generate_solutions.py` | Asks Qwen to solve each problem in numbered steps; stores attempts in `data/teach.db` |
| `generate_worked.py` | **The hints.** Qwen2.5-Math-1.5B (port 8084) solves each problem without knowing the answer; only solutions whose `\boxed{}` answer is right are kept. Their steps become the "Siguiente paso" help, never the step that states the answer |
| `generate_hints.py` | Experiments asking Qwen 0.8B for one-line hints (not used: they didn't help) |
| `generate_guided.py` | Experiment: the math model told the answer, with a fixed format (not used: it wrote empty plans) |
| `judge_errors.py` | Experiment: the math model points at the step where Qwen 0.8B goes wrong |
| `applab/canguro-led/` | App Lab app that shows the game on the LED matrix (paused for now) |
| `prepare_app.py` | Builds `data/app.db`: keeps usable attempts, gives each problem a topic and a similar easier problem with the embeddings |
| `make_demo_db.py` | `data/demo.db` with made-up problems, for front-end work |

## The API

All POST bodies are JSON. `uid` comes from `/api/login`.

| Endpoint | Body | Returns |
| --- | --- | --- |
| `GET /api/meta` | | topics (key, name, colour), tiers, problems per topic and tier, points per tier |
| `POST /api/login` | `name` | `uid`, `stats` |
| `POST /api/stats` | `uid` | per topic: `points` (0-100), `clean`/`helped` per tier, `need`, `cleared`, current `tier` (null = mastered); totals |
| `POST /api/history` | `uid` | `plays`: every finished problem in order with `total` after it and the `medal` it won, if any |
| `POST /api/next` | `uid`, `type` (or null), `mode` (`solve`/`correct`) | `play_id`, `problem` (text, `text_ca`, level, tier, year; steps and `qwen_answer` in correct mode), `help_kinds` |
| `POST /api/help` | `uid`, `play_id` | the next rung: `kind` and its content |
| `POST /api/sibling` | `uid`, `play_id`, `answer` | whether the answer to the similar problem is right |
| `POST /api/mark` | `uid`, `play_id`, `step` | marks a step in correct mode (also lights the LED matrix) |
| `POST /api/answer` | `uid`, `play_id`, `answer`, `step` (correct mode), `fix` | `right`, `value`, `points`, new `stats` when right |
| `POST /api/giveup` | `uid`, `play_id` | the correct value |
| `GET /api/led` | | the last event, for the App Lab app that draws on the LED matrix |
| `POST /api/report` | | for each kind of help: how often students solved the problem right after it |

The page never receives the correct answer before the student finds it or gives up.

## Design rules we follow

- Bands and blocks, not cards everywhere. One blue band for progress, one colour
  block per topic, the play screen takes the colour of its topic.
- Colours: blue band `#2349d6`, sun yellow `#ffc83d`, orange actions `#ff7a1a`;
  topic colours live in `kinds.py`. Bronze, silver and gold only mean medals.
- Fonts: Bahnschrift (ships with Windows) for headings and numbers, Segoe UI for
  text. No web fonts: the demo runs without internet.
- Icons are drawn SVG symbols at the top of the page, not emoji.
- Big touch targets (48 px or more), visible keyboard focus, works at phone width
  and in dark mode, one animation on load (and none with reduced motion).

## Data rules

M3Kang is licensed for research use only. `data/`, `*.db` and `*.gguf` are in
`.gitignore`: never force-add them. The demo problems in `make_demo_db.py` are
made up and can be shared.

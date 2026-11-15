# MateQwen: an offline Kangaroo math copilot on the Arduino UNO Q

*Text for the Arduino Project Hub write-up. Paste each section into the editor, and add the team's photos and screenshots where marked.*

---

## 1. Introduction

Mathematical proficiency in Catalonia is falling in international tests such as PISA. Teachers are stretched, and many students get lost in one-size-fits-all classrooms. AI can help, but only if it guides reasoning instead of doing the homework, and only if student data stays private.

MateQwen is a math copilot that runs **entirely on an Arduino UNO Q (4 GB)**, with no internet and no cloud:

- **It teaches.** Students train with real Kangaroo competition problems, in English and Catalan, on a web page served by the board. They type the answer (no options to guess from) and unlock help one step at a time. In a second mode, they find the step where a small AI model went wrong. The LED matrix shows each right answer, wrong answer and hint.
- **It reasons.** Given a Kangaroo problem with five options, it picks the right letter in under 60 seconds, using two small language models and a Python program it writes itself.

> 📷 Photo: the UNO Q with the LED matrix lit, next to a phone showing the app.
> 🎬 Video (30 s): a student solving a problem with one hint, and the matrix showing the tick.

## 2. Components and apps

**Hardware**
- Arduino UNO Q 4 GB
- USB-C cable and a 5 V / 3 A USB-C power supply
- A phone or laptop with a browser, for the student

**Software**
- [Arduino App Lab](https://www.arduino.cc/en/software/#app-lab-section)
- [llama.cpp](https://github.com/ggml-org/llama.cpp), built on the board at a pinned commit
- Models (GGUF, pinned by revision and SHA256):
  - Qwen3.5 0.8B Q8_0: fast letters and step-by-step attempts
  - Qwen2.5-Coder 1.5B Instruct Q4_K_M: Python programs
  - bge-small-en-v1.5 Q8_0: embeddings
  - Qwen2.5-Math 1.5B Instruct: offline only, to write the worked solutions behind the hints
- Dataset: [M3Kang](https://huggingface.co/datasets/qualcomm/M3Kang) (Torres-Camps et al., 2026, arXiv:2601.16218). It is for research use only, so it is not redistributed.

## 3. Architecture

> 🖼️ Diagram: copy the diagram from the README of the GitHub repository.

The UNO Q has two brains, and we use both:

- **Linux side (Qualcomm QRB2210):**
  - three `llama-server` processes, as systemd services that start at boot: the letter model on port 8081, the embeddings on 8082, and the code model on 8085;
  - `predict.py`, which answers problems;
  - the MateQwen web server on port 8000, which reads and writes a SQLite file.
- **Microcontroller:** an App Lab sketch draws on the 8×13 LED matrix. Its Python half polls the game's `/api/led` endpoint and sends one frame per event over the Bridge.

**Design rule: no model runs while a student plays.** On the board, generation runs at about 5 tokens per second, far too slow to wait for. So the models do their work ahead of time, also on the board: attempts, verified worked solutions, a topic for each problem, and a similar easier problem. All of it goes into SQLite, and every click answers in milliseconds.

**How we use M3Kang:**
- problem bank, in English and Catalan;
- topic map (six topics, from the embeddings);
- difficulty map (levels 0–7 grouped into three tiers with medals);
- similar easier problems (nearest neighbours at a lower level);
- verified worked solutions, which become the hints;
- the small model's wrong attempts, which become exercises;
- a statistical prior: among five numeric options, the middle one is correct three times as often as the smallest.

## 4. Step by step

The full commands are in the GitHub repository: `docs/SETUP.md` for the board, and `README.md` for our steps.

1. **Set up the board.** First setup in App Lab, then connect over SSH, or `adb shell` over USB. *You should see:* `arduino@<name>:~$`.
2. **Build llama.cpp and download the models.** Run `./board/01_install_packages.sh`, `./board/02_build_llama_cpp.sh` and `./board/03_download_models.sh`. *You should see:* every model "OK" against its SHA256.
3. **Install the services.** Run `./board/04_install_services.sh`. *You should see:* servers ready on 8081, 8082 and 8085, and MateQwen on port 8000.
4. **Prepare M3Kang on the laptop** with `laptop/prepare_m3kang.py`, then copy `data/` to the board.
5. **Prepare the teaching app** on the board: `teach/generate_solutions.py`, `teach/generate_worked.py`, then `teach/prepare_app.py --require-guide`. This takes hours, and can be resumed.
6. **Start the LED app.** Open `teach/applab/canguro-led` in App Lab and run it. Then make it the default app, so it starts at boot: `arduino-app-cli properties set default /home/arduino/ArduinoApps/canguro-led`. *You should see:* `arduino-app-cli properties get default` prints "Default app: Canguro Coach LED".
7. **Play.** Open `http://<board-ip>:8000` from a phone on the same Wi-Fi, or use `adb forward tcp:8000 tcp:8000` over USB.
8. **Check the reasoning.** Run `./board/06_exam_check.sh`, then `python3 eval.py --input data/demo/train.jsonl --limit 0 --name combo`.

> 📷 Screenshot for each mode: *Solve it yourself* with a hint open, and *Correct Qwen* with a step marked.

## 5. Results and error analysis

All runs on the board, with a 60 s limit per problem:

| Strategy | Problems | Accuracy |
| --- | --- | --- |
| Organizers' baseline (one letter) | 40 | 22.5% |
| One letter + program (`cascade`) | 100 practice, English + Catalan | 42% |
| `combo` (adds the M3Kang prior) | 64, balanced across the 8 levels | 34.4% |
| `cascade` against `combo`, same problems | 39 | 13 against 14 |

The slowest problem took 54 s. None went over the limit.

What the errors show:

- **By topic:** Numbers 53% and Counting 50%, but Geometry only 19%. Geometry is also the most common topic: most of these problems need the figure, which the text models never see.
- **By level:** 75% at level 0, and 12% at levels 3–5.
- **By language:** on the practice problems so far, English 42% (12 problems) and Catalan 36% (11 problems).
- **When the Python program finds an option, it is right 75% of the time.**
- **Letter bias:** the small model never answered E. The M3Kang prior corrects part of this.

## 6. What we learned, and what's next

- A small model on the edge is a poor judge of its own answers, but a good **source of exercises**: its wrong attempts make the *Correct Qwen* mode.
- **Code beats words** on a 1.5B model: a short program that lands on one of five options is right three times out of four.
- **What didn't work:**
  - Letting the code model check options with `ok(x)`: it decided 6 of 10 problems and got all 6 wrong.
  - A memory of repeated problems: it found no repeats in the test.
  - One-line hints from the 0.8B model.
- **Next:**
  - a vision model fast enough for geometry figures;
  - programs specialised by topic;
  - a classroom study of which hints actually help, measured with the app's `/api/report`.

**Team:** *(names)* · **Code:** *(GitHub link)* · **License:** *(MIT?)*

#!/usr/bin/env bash
# Before the official run: check that everything predict.py and MateQwen need
# is up, then run predict.py the way the organizers will, on a few practice
# problems and on awkward inputs, and check every answer is a letter in time.
#
#   ./board/06_exam_check.sh                     # 6 practice problems + edge cases
#   ./board/06_exam_check.sh data/demo/train.jsonl 100   # a longer run
#
# Exits with an error at the first thing that is wrong.
source "$(dirname "$0")/common.sh"
cd "$REPO_DIR"

INPUT="${1:-data/demo/train.jsonl}"
COUNT="${2:-6}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
failed=0
check() { if eval "$2"; then ok "$1"; else fail "$1"; failed=1; fi; }

step "Services (they must start at boot)"
for unit in math-llm math-embed math-code math-app; do
  check "$unit is running and enabled" "systemctl is-active --quiet $unit && systemctl is-enabled --quiet $unit"
done
check "language model answers on $LLM_PORT" "wait_for_health $LLM_PORT 60"
check "code model answers on $CODE_PORT" "wait_for_health $CODE_PORT 60"
check "MateQwen web page on $APP_PORT" "curl -sf -o /dev/null http://127.0.0.1:$APP_PORT/"
if [ "$APP_HOST" = 0.0.0.0 ]; then
  # Other devices need the app on every interface, not only on 127.0.0.1
  IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src") print $(i+1)}')"
  check "MateQwen listens on the network (APP_HOST=0.0.0.0)" \
    "ss -ltn | awk '{print \$4}' | grep -qx '0.0.0.0:$APP_PORT'"
  check "MateQwen opens at http://$IP:$APP_PORT" "curl -sf -o /dev/null http://$IP:$APP_PORT/"
fi
check "predict.py strategy is cascade" "[ \"$STRATEGY\" = cascade ]"
free -m | awk 'NR==2 {printf "  RAM: %d MB used, %d MB available\n", $3, $7}'

step "predict.py on $COUNT problems of $INPUT"
[ -f "$INPUT" ] || die "$INPUT not found (prepare it with laptop/prepare_m3kang.py and copy it here)"
# A mix: both languages, text and figures, easy and hard
python3 - "$INPUT" "$COUNT" > "$TMP/sample.jsonl" <<'EOF'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
n = int(sys.argv[2])
step = max(1, len(rows) // n)
for r in rows[::step][:n]:
    print(json.dumps(r, ensure_ascii=False))
EOF
python3 predict.py --input "$TMP/sample.jsonl" --output "$TMP/sample.csv"
python3 - "$TMP/sample.jsonl" "$TMP/sample.csv" <<'EOF' || failed=1
import csv, json, sys
probs = [json.loads(l) for l in open(sys.argv[1])]
rows = list(csv.DictReader(open(sys.argv[2])))
assert len(rows) == len(probs), f"{len(rows)} answers for {len(probs)} problems"
bad = [r for r in rows if r["answer"] not in list("ABCDE")]
slow = [r for r in rows if float(r["seconds"]) >= 60]
right = sum(r["answer"] == p.get("label") for r, p in zip(rows, probs))
print(f"  {len(rows)} answers, {right} right, slowest {max(float(r['seconds']) for r in rows):.1f} s, "
      f"{sum(r['status'] == 'ok:code' for r in rows)} from a program")
assert not bad, f"not a letter: {bad}"
assert not slow, f"60 s or more: {slow}"
print("  OK    every answer is a letter, all under 60 s")
EOF

step "predict.py on awkward inputs"
cat > "$TMP/edge.jsonl" <<'EOF'
{"id": "edge-no-options", "text": "1. How many sides does a hexagon have?"}
{"id": "edge-empty-options", "language": "cat", "text": "2. Quants costats té un hexàgon?", "options": {}}
{"id": "edge-words", "language": "eng", "text": "3. Who is tallest?", "options": {"A": "Ann", "B": "Bob", "C": "Cat", "D": "Dan", "E": "Eve"}}
{"id": "edge-ocr", "language": "eng", "text": "4. What is 3 - 3?", "options": {"A": "O", "B": "1", "C": "2", "D": "一3", "E": "36cm2"}}
{"id": "edge-four-options", "language": "eng", "text": "5. What is 2 + 2?", "options": {"A": "3", "B": "4", "C": "5", "D": "6"}}
EOF
python3 predict.py --input "$TMP/edge.jsonl" --output "$TMP/edge.csv"
check "an answer for each awkward input, all letters" \
  "python3 -c \"import csv; r=list(csv.DictReader(open('$TMP/edge.csv'))); assert len(r)==5 and all(x['answer'] in 'ABCDE' and len(x['answer'])==1 for x in r)\""

step "If the code model is down, predict.py still answers"
CODE_PORT=1 python3 predict.py --input "$TMP/edge.jsonl" --output "$TMP/nocode.csv" 2>/dev/null
check "letters without the code model" \
  "python3 -c \"import csv; r=list(csv.DictReader(open('$TMP/nocode.csv'))); assert len(r)==5 and all(x['answer'] in 'ABCDE' for x in r)\""

step "MateQwen end to end (made-up problems, its own port)"
python3 teach/make_demo_db.py >/dev/null  # data/demo.db, separate from the real data/app.db
python3 teach/server.py --db data/demo.db --port 8001 >/dev/null 2>&1 &
APP_PID=$!
sleep 2
python3 teach/test_api.py --url http://127.0.0.1:8001 | tail -3 || failed=1
kill "$APP_PID" 2>/dev/null || true

echo
if [ "$failed" = 0 ]; then ok "${BOLD}All checks passed: ready for the official run${RESET}"
else die "some checks failed (see FAIL above)"; fi

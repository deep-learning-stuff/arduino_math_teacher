#!/usr/bin/env bash
# Run the three llama-servers and the MateQwen web app as systemd services, so
# they start at boot and restart if they crash. The model servers listen on 127.0.0.1
# only; the web app listens on APP_HOST (config.env).
# Run it again whenever you change config.env.
source "$(dirname "$0")/common.sh"

[ -x "$LLAMA_SERVER" ] || die "llama-server not found. Run ./board/02_build_llama_cpp.sh first."
[ -f "$LLM_PATH" ] && [ -f "$EMBED_PATH" ] && [ -f "$CODE_PATH" ] || die "models not found. Run ./board/03_download_models.sh first."

# A llama-server you started by hand would hold the port and stop the service
for port in "$LLM_PORT" "$EMBED_PORT" "$CODE_PORT"; do
  pid="$(pgrep -f -- "llama-server.*--port $port" | head -1 || true)"
  service_pids="$(systemctl show -p MainPID --value math-llm math-embed math-code 2>/dev/null || true)"
  if [ -n "$pid" ] && [[ $'\n'"$service_pids"$'\n' != *$'\n'"$pid"$'\n'* ]]; then
    die "a llama-server you started by hand (PID $pid) is using port $port. Stop it with: kill $pid"
  fi
done

render() {
  sed -e "s|@USER@|$USER|g" \
      -e "s|@LLAMA_SERVER@|$LLAMA_SERVER|g" \
      -e "s|@LLM_PATH@|$LLM_PATH|g" -e "s|@LLM_PORT@|$LLM_PORT|g" \
      -e "s|@LLM_THREADS@|$LLM_THREADS|g" -e "s|@LLM_CTX@|$LLM_CTX|g" -e "s|@LLM_ALIAS@|$LLM_ALIAS|g" \
      -e "s|@EMBED_PATH@|$EMBED_PATH|g" -e "s|@EMBED_PORT@|$EMBED_PORT|g" \
      -e "s|@EMBED_THREADS@|$EMBED_THREADS|g" -e "s|@EMBED_CTX@|$EMBED_CTX|g" \
      -e "s|@CODE_PATH@|$CODE_PATH|g" -e "s|@CODE_PORT@|$CODE_PORT|g" \
      -e "s|@CODE_THREADS@|$CODE_THREADS|g" -e "s|@CODE_CTX@|$CODE_CTX|g" \
      -e "s|@REPO_DIR@|$REPO_DIR|g" -e "s|@APP_PORT@|$APP_PORT|g" -e "s|@APP_HOST@|$APP_HOST|g" \
      "$REPO_DIR/board/systemd/$1"
}

step "Installing the math-llm, math-embed, math-code and math-app services"
for unit in math-llm.service math-embed.service math-code.service math-app.service; do
  render "$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable math-llm math-embed math-code math-app
sudo systemctl restart math-llm math-embed math-code math-app

step "Waiting for the servers to load the models"
wait_for_health "$LLM_PORT" 180 || die "the LLM server didn't start. See: journalctl -u math-llm -n 50"
ok "LLM server ready on 127.0.0.1:$LLM_PORT"
wait_for_health "$EMBED_PORT" 60 || die "the embedding server didn't start. See: journalctl -u math-embed -n 50"
ok "embedding server ready on 127.0.0.1:$EMBED_PORT"
wait_for_health "$CODE_PORT" 180 || die "the code model server didn't start. See: journalctl -u math-code -n 50"
ok "code model server ready on 127.0.0.1:$CODE_PORT"
sleep 2
if systemctl is-active --quiet math-app; then ok "MateQwen web app on $APP_HOST:$APP_PORT (from another device: http://$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src") print $(i+1)}'):$APP_PORT)"
else warn "MateQwen did not start. See: journalctl -u math-app -n 50"; fi
free -m | awk 'NR==2 {printf "  RAM: %d MB used, %d MB available\n", $3, $7}'
ok "Next: ./board/05_smoke_test.sh"

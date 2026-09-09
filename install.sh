#!/usr/bin/env bash
# Install the DGX Spark model card sidecar.
#
# Touches nothing under /opt/nvidia/. Verifies that afterwards.
set -euo pipefail

PREFIX=/opt/local/dgx-model-card
UNIT=dgx-model-card.service
SERVICE_USER="${SUDO_USER:-$USER}"
PORT=8110
GATE_PORT=8111
MARGIN=10
SWAP_URL=http://127.0.0.1:8100
SWAP_CONFIG=/etc/llama-swap/config.yaml
MODEL=""
APT_NOTICE=1

usage() {
  cat <<EOF
usage: sudo ./install.sh [options]

  --user <name>          run as this user            (default: ${SERVICE_USER})
  --port <n>             card + API port             (default: ${PORT})
  --gate-port <n>        enforcing proxy port        (default: ${GATE_PORT}; 0 disables)
  --margin <gib>         memory kept free            (default: ${MARGIN})
  --swap-url <url>       llama-swap                  (default: ${SWAP_URL})
  --swap-config <path>   llama-swap config.yaml      (default: ${SWAP_CONFIG})
  --model <id>           initial selection           (default: first model found)
  --no-apt-notice        skip the dgx-dashboard upgrade reminder
  -h, --help
EOF
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --user) SERVICE_USER=$2; shift 2;;
    --port) PORT=$2; shift 2;;
    --gate-port) GATE_PORT=$2; shift 2;;
    --margin) MARGIN=$2; shift 2;;
    --swap-url) SWAP_URL=$2; shift 2;;
    --swap-config) SWAP_CONFIG=$2; shift 2;;
    --model) MODEL=$2; shift 2;;
    --no-apt-notice) APT_NOTICE=0; shift;;
    -h|--help) usage;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

echo "==> preflight"
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }
python3 - <<'PY' || { echo "python 3.10+ required" >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
id "$SERVICE_USER" >/dev/null 2>&1 || { echo "no such user: $SERVICE_USER" >&2; exit 1; }
[ -r "$SWAP_CONFIG" ] || echo "    warning: $SWAP_CONFIG not readable — the memory guard will run permissive"
if ! curl -fsS -m 5 "$SWAP_URL/v1/models" >/dev/null 2>&1; then
  echo "    warning: llama-swap not answering at $SWAP_URL — installing anyway"
fi
for p in "$PORT" "$GATE_PORT"; do
  [ "$p" = 0 ] && continue
  if ss -ltn 2>/dev/null | grep -q ":$p "; then echo "port $p already in use" >&2; exit 1; fi
done

echo "==> baseline (so we can prove we did not touch NVIDIA)"
BASE=$(mktemp -d)
dpkg -V dgx-dashboard > "$BASE/dpkg-V.before" 2>&1 || true
sha256sum /opt/nvidia/dgx-dashboard-service/dashboard-service > "$BASE/sha.before" 2>/dev/null || true

echo "==> files -> $PREFIX"
install -d -o root -g root -m 0755 "$PREFIX" "$PREFIX/web"
install -o root -g root -m 0755 "$HERE/src/model_card.py"   "$PREFIX/model_card.py"
install -o root -g root -m 0644 "$HERE/src/web/card.js"     "$PREFIX/web/card.js"
install -o root -g root -m 0644 "$HERE/src/web/index.html"  "$PREFIX/web/index.html"

echo "==> unit -> /etc/systemd/system/$UNIT"
sed -e "s#^User=.*#User=${SERVICE_USER}#" \
    -e "s#^Group=.*#Group=${SERVICE_USER}#" \
    -e "s#^Environment=NC_PORT=.*#Environment=NC_PORT=${PORT}#" \
    -e "s#^Environment=NC_GATE_PORT=.*#Environment=NC_GATE_PORT=${GATE_PORT}#" \
    -e "s#^Environment=NC_GATE=.*#Environment=NC_GATE=$([ "$GATE_PORT" = 0 ] && echo 0 || echo 1)#" \
    -e "s#^Environment=NC_MARGIN_GIB=.*#Environment=NC_MARGIN_GIB=${MARGIN}#" \
    -e "s#^Environment=NC_SWAP_URL=.*#Environment=NC_SWAP_URL=${SWAP_URL}#" \
    -e "s#^Environment=NC_SWAP_CONFIG=.*#Environment=NC_SWAP_CONFIG=${SWAP_CONFIG}#" \
    "$HERE/packaging/dgx-model-card.service" > "/etc/systemd/system/$UNIT"

if [ -n "$MODEL" ]; then
  sed -i "s#^Environment=NC_MODEL=.*#Environment=NC_MODEL=${MODEL}#" "/etc/systemd/system/$UNIT"
else
  sed -i "/^Environment=NC_MODEL=/d" "/etc/systemd/system/$UNIT"
fi
chmod 0644 "/etc/systemd/system/$UNIT"

if [ "$APT_NOTICE" = 1 ] && [ -d /etc/apt/apt.conf.d ]; then
  install -o root -g root -m 0644 \
    "$HERE/packaging/99-dgx-model-card-apt-notice" /etc/apt/apt.conf.d/99-dgx-model-card-apt-notice
  echo "    apt reminder installed"
fi

echo "==> start"
systemctl daemon-reload
systemctl enable --now "$UNIT"
sleep 2
systemctl is-active --quiet "$UNIT" || { echo "service failed to start:" >&2; journalctl -u "$UNIT" -n 20 --no-pager >&2; exit 1; }

echo "==> verify"
curl -fsS -m 5 "http://127.0.0.1:${PORT}/healthz" && echo
if [ "$GATE_PORT" != 0 ]; then
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 8 "http://127.0.0.1:${GATE_PORT}/v1/models" || true)
  echo "    gate :${GATE_PORT}/v1/models -> HTTP ${code}"
fi

dpkg -V dgx-dashboard > "$BASE/dpkg-V.after" 2>&1 || true
sha256sum /opt/nvidia/dgx-dashboard-service/dashboard-service > "$BASE/sha.after" 2>/dev/null || true
if diff -q "$BASE/dpkg-V.before" "$BASE/dpkg-V.after" >/dev/null 2>&1 &&
   diff -q "$BASE/sha.before" "$BASE/sha.after" >/dev/null 2>&1; then
  echo "    NVIDIA package unchanged (dpkg -V and binary hash identical)"
else
  echo "    WARNING: NVIDIA state differs from before this install — investigate" >&2
fi
rm -rf "$BASE"

cat <<EOF

Done.

  card       http://127.0.0.1:${PORT}
  API        http://127.0.0.1:${PORT}/api/status
$([ "$GATE_PORT" != 0 ] && echo "  gate       http://127.0.0.1:${GATE_PORT}  (point your reverse proxy here instead of llama-swap)")

Next:
  1. install userscript/dgx-model-card.user.js in Violentmonkey or Tampermonkey
  2. open the dashboard and sign in

Remote access needs both ports forwarded:
  ssh -L 11000:127.0.0.1:11000 -L ${PORT}:127.0.0.1:${PORT} ${SERVICE_USER}@\$(hostname)

Uninstall:
  sudo systemctl disable --now ${UNIT}
  sudo rm -rf ${PREFIX} /etc/systemd/system/${UNIT} /etc/apt/apt.conf.d/99-dgx-model-card-apt-notice
  sudo systemctl daemon-reload
EOF

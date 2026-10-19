#!/usr/bin/env bash
# Install or update sparkdash-api on one Spark (mgllm/node-api): its files in /opt/sparkdash-api, the
# no-login user sparkdash-api, /etc/sparkdash-api/env (root, 0600), its systemd unit and the GPU clock control's
# root helper (sparkdash-clock.path + .service); then it checks the service from the Spark itself. It never restarts or touches the engine; the tunnel firewall is a separate step (README).
#
#   mgllm/scripts/install-node-api.sh user@spark
#
# The token lives in $SPARKDASH_API_ENV (default ~/.config/sparkdash-api.env, outside any repo; made here on first use) and travels over ssh's stdin,
# never on a command line or in output. Every Spark and the gateway's sparkDash share it.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)                       # mgllm
REPO=$(cd "$HERE/.." && pwd)                                 # this fork
TARGET=${1:?usage: $0 user@spark}
SECRETS=${SPARKDASH_API_ENV:-$HOME/.config/sparkdash-api.env}
PORT=9701

if [[ ! -f $SECRETS ]]; then
  (umask 077; printf 'SPARKDASH_API_TOKEN=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" > "$SECRETS")
  echo "install-node-api: new token in $SECRETS"
fi
TOKEN=$(sed -n 's/^SPARKDASH_API_TOKEN=//p' "$SECRETS")
[[ ${#TOKEN} -ge 32 ]] || { echo "install-node-api: no token in $SECRETS" >&2; exit 2; }

(cd "$HERE/node-api" && python3 -m py_compile sparkdash_api.py)
STAGE=$(ssh "$TARGET" mktemp -d)
scp -q "$HERE"/node-api/{sparkdash_api.py,sparkdash_clock.py,known-commands.json,sparkdash-api.service} \
    "$HERE"/node-api/{sparkdash-clock.path,sparkdash-clock.service} "$HERE"/node-api/vendor/pynvml.py \
    "$TARGET:$STAGE/"
{ cat "$SECRETS"; printf 'SPARKDASH_API_BIND=0.0.0.0\nSPARKDASH_API_PORT=%s\n' "$PORT"; } |
  ssh "$TARGET" 'sudo install -d -m 0700 /etc/sparkdash-api && sudo sh -c "umask 077; cat > /etc/sparkdash-api/env"'
ssh "$TARGET" "sudo bash -s" <<EOF
set -euo pipefail
id sparkdash-api >/dev/null 2>&1 ||
  useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin sparkdash-api
install -d -m 0755 /opt/sparkdash-api /opt/sparkdash-api/vendor
install -m 0644 $STAGE/sparkdash_api.py $STAGE/sparkdash_clock.py $STAGE/known-commands.json /opt/sparkdash-api/
install -m 0644 $STAGE/pynvml.py /opt/sparkdash-api/vendor/pynvml.py
install -m 0644 $STAGE/sparkdash-api.service $STAGE/sparkdash-clock.path $STAGE/sparkdash-clock.service /etc/systemd/system/
rm -rf "$STAGE"
python3 -m py_compile /opt/sparkdash-api/sparkdash_api.py /opt/sparkdash-api/sparkdash_clock.py
systemctl daemon-reload
systemctl enable sparkdash-api >/dev/null 2>&1
systemctl restart sparkdash-api
systemctl enable --now sparkdash-clock.path >/dev/null 2>&1
EOF

# the check, from the Spark itself (the unit admits localhost): health, one known line with the token, one unknown
# line, one request without the token. The token comes in on stdin and reaches curl through a file descriptor, so it
# is in no argv.
CHECK=$(cat <<'CHK'
set -u
port=$1
read -r auth
hdr() { printf '%s\n' "$auth"; }
url=http://127.0.0.1:$port/sparkdashapi
for i in $(seq 1 20); do curl -fs -m 2 "$url/health" >/dev/null && break; sleep 0.5; done
echo "health: $(curl -s -m 3 "$url/health")"
echo "known line: $(curl -s -m 5 -H @<(hdr) -H 'Content-Type: application/json' -d '{"cmd":"echo ok"}' "$url/exec")"
echo "unknown line: HTTP $(curl -s -o /dev/null -w '%{http_code}' -m 5 -H @<(hdr) -H 'Content-Type: application/json' -d '{"cmd":"id"}' "$url/exec")"
echo "no token: HTTP $(curl -s -o /dev/null -w '%{http_code}' -m 5 -H 'Content-Type: application/json' -d '{"cmd":"echo ok"}' "$url/exec")"
echo "unit: $(systemctl is-active sparkdash-api) / $(systemctl is-enabled sparkdash-api); memory $(systemctl show sparkdash-api -p MemoryCurrent --value) bytes"
echo "clock: path $(systemctl is-active sparkdash-clock.path); now $(curl -s -m 5 -H @<(hdr) -H 'Content-Type: application/json' -d '{"cmd":"mgllm:gpu-clock"}' "$url/exec")"
CHK
)
printf 'Authorization: Bearer %s\n' "$TOKEN" | ssh "$TARGET" "bash -c $(printf '%q' "$CHECK") check $PORT"

#!/usr/bin/env bash
# Build this fork's sparkDash on this workstation (npm over one connection) and run it on the
# dashboard host at https://<your host>/sparkdash/ behind a reverse proxy's password (mgllm/README.md).
# It never touches LiteLLM or the engines; Caddy is reloaded (graceful), never restarted, and only after the edited
# Caddyfile validates.
#
#   mgllm/scripts/deploy-gateway.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)                       # mgllm
REPO=$(cd "$HERE/.." && pwd)                                 # this fork
GW=${GATEWAY:?set GATEWAY=user@dashboard-host}
SSH=(ssh -p "${GATEWAY_SSH_PORT:-22}" -o BatchMode=yes "$GW")
WORK=${WORK:-$REPO}                                          # this fork's checkout
export PATH=${NODE_BIN:-$HOME/.nvm/versions/node/v22.21.1/bin}:$PATH
TOKEN=$(sed -n 's/^SPARKDASH_API_TOKEN=//p' "${SPARKDASH_API_ENV:-$HOME/.config/sparkdash-api.env}")
[[ ${#TOKEN} -ge 32 ]] || { echo "deploy-gateway: no token (scripts/install-node-api.sh makes it)" >&2; exit 2; }

# 1. the source: this fork's commit, in a throwaway worktree
BUILD=$(mktemp -d)
trap 'git -C "$WORK" worktree remove --force "$BUILD/src" 2>/dev/null; rm -rf "$BUILD"' EXIT
git -C "$WORK" worktree add -q --detach "$BUILD/src" HEAD
TAG=sparkdash-mgllm:$(git -C "$WORK" rev-parse --short=12 HEAD)

# 2. built at /sparkdash/, then only the production dependencies kept
(cd "$BUILD/src" &&
  npm ci --maxsockets=1 --prefer-offline --no-audit --no-fund --loglevel=error >/dev/null &&
  VITE_BASE=/sparkdash/ npm run -s build >/dev/null &&
  npm prune --omit=dev --no-audit --no-fund --loglevel=error >/dev/null)

# 3. the image's app: what her own Dockerfile's runtime stage copies (dist twice, server, node_modules, config, the
#    shared sources the server imports), without the SSH and host tools this setup does not use
APP=$BUILD/ctx/app
mkdir -p "$APP/src/components/ShowcasePage"
cp -a "$BUILD/src"/{dist,server,node_modules,config,package.json,package-lock.json} "$APP/"
cp -a "$BUILD/src/dist" "$APP/dist-baked"
cp -a "$BUILD/src/src/shared" "$APP/src/shared"
cp "$BUILD/src/src/components/ShowcasePage/showcasePrompts.ts" "$APP/src/components/ShowcasePage/"
cp "$HERE/deploy/Dockerfile.gateway" "$BUILD/ctx/Dockerfile"
tar -C "$BUILD/ctx" -czf "$BUILD/ctx.tgz" .
echo "deploy-gateway: built $TAG ($(du -sh "$BUILD/ctx.tgz" | cut -f1) to send)"

# 4. on the gateway: the image, its config and env, the unit; then Caddy, validated before it is reloaded
scp -q -P "${GATEWAY_SSH_PORT:-22}" "$BUILD/ctx.tgz" "$HERE"/deploy/{mgllm-sparkdash.service,sparks.json,settings.json,caddy-sparkdash.py} \
    "$GW:/tmp/"
{ printf 'SPARKDASH_EXEC_TOKEN=%s\n' "$TOKEN"; grep -v '^#' "$HERE/deploy/env"; } |
  "${SSH[@]}" 'sudo install -d -m 0700 /etc/sparkdash && sudo sh -c "umask 077; cat > /etc/sparkdash/env"'
"${SSH[@]}" "sudo bash -s" <<EOF
set -euo pipefail
B=\$(mktemp -d)
tar -C "\$B" -xzf /tmp/ctx.tgz
docker build -q -t $TAG -t sparkdash-mgllm:current "\$B" >/dev/null
rm -rf "\$B" /tmp/ctx.tgz
install -d -o 1000 -g 1000 -m 0750 /var/lib/sparkdash /var/lib/sparkdash/config
install -o 1000 -g 1000 -m 0640 /tmp/sparks.json /var/lib/sparkdash/config/
# settings.json only the first time: after that the page's Settings own it (energy price, density, ...)
[ -e /var/lib/sparkdash/config/settings.json ] || install -o 1000 -g 1000 -m 0640 /tmp/settings.json /var/lib/sparkdash/config/
install -m 0644 /tmp/mgllm-sparkdash.service /etc/systemd/system/mgllm-sparkdash.service
rm -f /tmp/sparks.json /tmp/settings.json /tmp/mgllm-sparkdash.service
systemctl daemon-reload
systemctl enable mgllm-sparkdash >/dev/null 2>&1
systemctl restart mgllm-sparkdash
cp /etc/caddy/Caddyfile /tmp/Caddyfile.new
python3 /tmp/caddy-sparkdash.py /tmp/Caddyfile.new
rm -f /tmp/caddy-sparkdash.py
if ! cmp -s /tmp/Caddyfile.new /etc/caddy/Caddyfile; then
  caddy validate --config /tmp/Caddyfile.new --adapter caddyfile >/dev/null 2>&1 ||
    { echo "deploy-gateway: the edited Caddyfile does not validate; the live one is untouched" >&2; exit 3; }
  cp -p /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-\$(date +%Y%m%d-%H%M%S)-sparkdash
  install -m 0644 /tmp/Caddyfile.new /etc/caddy/Caddyfile
  systemctl reload caddy
  echo "deploy-gateway: Caddy reloaded with /sparkdash/"
fi
rm -f /tmp/Caddyfile.new
echo "deploy-gateway: \$(systemctl is-active mgllm-sparkdash) running $TAG"
EOF

"""Write sparkDash's /sparkdash/ block into a copy of the gateway's Caddyfile, right after /dashboard/'s, with the
same basic_auth users (copied, never typed). Every write method is refused there except the GPU clock cap's
POST /api/gpu-clock (presets only, checked by the app) and the Settings dialog's PUT /api/settings (display
preferences such as the energy price, whitelisted and clamped by the app; none reaches a Spark). Idempotent: an
existing block is replaced, so a password changed in /dashboard/ follows. scripts/deploy-gateway.sh validates the copy
with caddy before it replaces the live file (backed up) and reloads Caddy."""
import re, sys
path = sys.argv[1]
text = open(path).read()
m = re.search(r"\thandle /dashboard/\* \{\n\t\tbasic_auth \{\n(?P<users>.*?)\n\t\t\}\n.*?\n\t\}\n", text, re.S)
if not m:
    sys.exit("caddy: no /dashboard/ block with basic_auth to copy")
block = (
    "\n\t# sparkDash (mgllm, 2026-10-10): MiaAI-Lab's dashboard, reading the Sparks through their\n"
    "\t# sparkdash-api. The same users as /dashboard/. Every write (benchmarks, model launches, shutdown, settings)\n"
    "\t# is refused before it reaches the app (127.0.0.1:5555), except the GPU clock cap (POST /api/gpu-clock) and\n"
    "\t# the Settings dialog's display preferences (PUT /api/settings).\n"
    "\t# Caddy runs uri before respond, so @write sees the path with /sparkdash already stripped.\n"
    "\tredir /sparkdash /sparkdash/\n"
    "\thandle /sparkdash/* {\n"
    "\t\tbasic_auth {\n" + m.group("users") + "\n\t\t}\n"
    "\t\t@write {\n"
    "\t\t\tmethod POST PUT PATCH DELETE\n"
    "\t\t\tnot {\n"
    "\t\t\t\tmethod POST\n"
    "\t\t\t\tpath /api/gpu-clock\n"
    "\t\t\t}\n"
    "\t\t\tnot {\n"
    "\t\t\t\tmethod PUT\n"
    "\t\t\t\tpath /api/settings\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t\trespond @write \"sparkDash is read-only here\" 403\n"
    "\t\turi strip_prefix /sparkdash\n"
    "\t\treverse_proxy 127.0.0.1:5555\n"
    "\t}\n")
old = re.compile(r"\n\t# sparkDash \(mgllm.*?\n\thandle /sparkdash/\* \{\n.*?\n\t\}\n", re.S)
if old.search(text):
    text = old.sub(lambda _: block, text, count=1)
    print("caddy: /sparkdash/ block replaced")
elif "/sparkdash" in text:
    sys.exit("caddy: a /sparkdash block this script did not write is there; edit it by hand")
else:
    text = text[:m.end()] + block + text[m.end():]
    print("caddy: /sparkdash/ block inserted after /dashboard/")
open(path, "w").write(text)

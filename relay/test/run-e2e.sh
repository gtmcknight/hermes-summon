#!/bin/bash
# Relay end-to-end: fake X on :8790, the relay in wrangler dev on :8789 (local Durable Object, test
# secrets, 2 free replies), then the plugin's setup and adapter against both.
set -euo pipefail
cd "$(dirname "$0")/.."
WRANGLER="${WRANGLER:-npx wrangler}"
STATE=$(mktemp -d)
python3 test/fake_x.py 8790 &
FAKE=$!
printf 'X_CLIENT_ID=client123\nX_CLIENT_SECRET=secret456\n' > .dev.vars  # fake test secrets
WRANGLER_SEND_METRICS=false $WRANGLER dev --port 8789 --persist-to "$STATE/do" \
  --var X_API:http://127.0.0.1:8790 --var X_AUTH_URL:http://127.0.0.1:8790/authorize \
  --var PUBLIC_ORIGIN:http://127.0.0.1:8789 --var FREE_REPLIES:2 --var BORROW_BOT:taybotfyi --var BORROW_FREE:2 --var BORROW_CODE:hermes-judge --var ADMIN_KEY:test-admin >"$STATE/wrangler.log" 2>&1 &
RELAY=$!
trap 'kill $FAKE $RELAY 2>/dev/null; rm -rf "$STATE" .dev.vars' EXIT
for _ in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:8789/ && break; sleep 0.5; done
python3 test/e2e.py http://127.0.0.1:8789 http://127.0.0.1:8790 || { tail -30 "$STATE/wrangler.log"; exit 1; }

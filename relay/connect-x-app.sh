#!/bin/bash
# One time: put the Summon X app's OAuth 2.0 client ID and secret on the relay, deploy it to
# https://summon.taylor.fyi, and check it answers. Run it in any terminal.
set -euo pipefail
cd "$(dirname "$0")"
W="$HOME/Documents/GitHub/xbot/node_modules/.bin/wrangler"
[ -x "$W" ] || W="npx -y wrangler@4.153.0"
read -r -p "Summon app OAuth 2.0 Client ID: " CID
read -r -s -p "Summon app OAuth 2.0 Client Secret (stays hidden): " CSECRET; echo
if [ -z "$CID" ] || [ -z "$CSECRET" ]; then echo "Both are needed. Nothing changed."; exit 1; fi
WRANGLER_SEND_METRICS=false $W deploy
printf '%s' "$CID" | WRANGLER_SEND_METRICS=false $W secret put X_CLIENT_ID >/dev/null
printf '%s' "$CSECRET" | WRANGLER_SEND_METRICS=false $W secret put X_CLIENT_SECRET >/dev/null
echo "Secrets saved. Waiting for https://summon.taylor.fyi ..."
for _ in $(seq 1 45); do
  if curl -fsS https://summon.taylor.fyi/ 2>/dev/null | grep -q Summon; then echo "Relay is live: https://summon.taylor.fyi"; exit 0; fi
  sleep 2
done
echo "Deployed, but summon.taylor.fyi isn't answering yet (DNS can take a few minutes)."

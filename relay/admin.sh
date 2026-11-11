#!/bin/bash
# Taylor's switches for the borrowed bot. The admin key is read from the Keychain item "summon-admin"
# (add it once: security add-generic-password -a summon -s summon-admin -w).
#   admin.sh status | off | on | ban <handle> | unban <handle>
set -euo pipefail
RELAY="${SUMMON_RELAY:-https://summon.taylor.fyi}"
KEY=$(security find-generic-password -a summon -s summon-admin -w 2>/dev/null || true)
[ -n "$KEY" ] || { echo "No admin key in the Keychain. Add it: security add-generic-password -a summon -s summon-admin -w"; exit 1; }
H=(-H "authorization: Bearer $KEY" -H "content-type: application/json")
case "${1:-status}" in
  status) curl -sS "${H[@]}" "$RELAY/admin/borrow" ;;
  off)    curl -sS "${H[@]}" -X POST -d '{"off":true}' "$RELAY/admin/borrow" ;;
  on)     curl -sS "${H[@]}" -X POST -d '{"off":false}' "$RELAY/admin/borrow" ;;
  ban)    curl -sS "${H[@]}" -X POST -d "{\"owner\":\"${2:?handle}\",\"banned\":true}" "$RELAY/admin/ban" ;;
  unban)  curl -sS "${H[@]}" -X POST -d "{\"owner\":\"${2:?handle}\",\"banned\":false}" "$RELAY/admin/ban" ;;
  *) echo "usage: admin.sh status | off | on | ban <handle> | unban <handle>"; exit 1 ;;
esac
echo

#!/usr/bin/env bash
# Canned log queries for the game platform. Each prints JSON that you (or an agent) can read
# or pipe into tools/log_digest.py. Needs gcloud, signed in to the project.
#
#   ./investigate.sh alerts               # every minor and major alert, newest first
#   ./investigate.sh errors               # errors and crashes, with their request arguments
#   ./investigate.sh slow                 # slow and stuck requests, with their arguments
#   ./investigate.sh game                 # game functions that timed out, ran out of memory or failed
#   ./investigate.sh illegal              # rejected moves
#   ./investigate.sh signups              # surges and floods of new accounts
#   ./investigate.sh request 3f9c2a1b7d4e8a60   # everything one request did
#   ./investigate.sh match MATCH_ID       # everything about one match
#   ./investigate.sh digest               # the summary of the last hours
# Options through the environment: PROJECT_ID, FRESHNESS (default 6h), LIMIT (default 500).
set -euo pipefail
FRESHNESS="${FRESHNESS:-6h}"
LIMIT="${LIMIT:-500}"
BASE='log_id("game_platform")'
read_logs() { gcloud ${PROJECT_ID:+--project="$PROJECT_ID"} logging read "$1" --freshness="$FRESHNESS" --limit="$LIMIT" --format=json; }

case "${1:-}" in
  alerts)  read_logs "$BASE AND jsonPayload.alert=(\"minor\" OR \"major\")" ;;
  errors)  read_logs "$BASE AND severity>=ERROR" ;;
  slow)    read_logs "$BASE AND jsonPayload.event=(\"request_slow\" OR \"request_very_slow\" OR \"request_stuck\")" ;;
  game)    read_logs "$BASE AND jsonPayload.event=(\"game_function_timeout\" OR \"game_function_memory\" OR \"game_function_error\" OR \"computer_illegal_move\" OR \"game_function_slow\")" ;;
  illegal) read_logs "$BASE AND jsonPayload.event=\"illegal_move\"" ;;
  signups) read_logs "$BASE AND jsonPayload.event=(\"signup_surge\" OR \"signup_flood\")" ;;
  request) read_logs "$BASE AND (jsonPayload.request_id=\"${2:?a request id}\" OR jsonPayload.request_id_stuck=\"$2\")" ;;
  match)   read_logs "$BASE AND jsonPayload.match_id=\"${2:?a match id}\"" ;;
  digest)  read_logs "$BASE" | python3 "$(dirname "${BASH_SOURCE[0]}")/../../tools/log_digest.py" - ;;
  *) sed -n '2,13p' "${BASH_SOURCE[0]}"; exit 1 ;;
esac

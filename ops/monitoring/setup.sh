#!/usr/bin/env bash
# Creates the notification channels, the uptime check and the alert policies for the
# game platform in a Google Cloud project. Run it from Cloud Shell (or any machine with
# gcloud and python3, signed in as someone who can edit Monitoring).
#
#   PROJECT_ID=my-project ALERT_EMAIL=me@example.com ./setup.sh
#
# Optional, to also page for major alerts:
#   PAGERDUTY_KEY=...        a PagerDuty "Google Cloud" integration (service) key
#   SMS_NUMBER=+15551234567  a phone to text (Google may ask you to verify it in the console)
#   MAJOR_EXTRA_CHANNELS=projects/P/notificationChannels/ID,...  channels you made in the console
# HOST (default buildplay.fun) is the server the uptime check watches.
#
# Safe to run again: it skips what already exists (by name). Nothing here touches the VM.
# The VM side (Ops Agent, JSON log file) is in docs/monitoring.md, step 2.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
: "${ALERT_EMAIL:?set ALERT_EMAIL (minor alerts are emailed here)}"
HOST="${HOST:-buildplay.fun}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${OUT:-$(mktemp -d)}"
gc() { gcloud --project="$PROJECT_ID" "$@"; }

echo "== Enabling the APIs"
gc services enable monitoring.googleapis.com logging.googleapis.com

channel_named() {  # prints the full name of the channel with this display name, if any
  gc beta monitoring channels list --filter="displayName=\"$1\"" --format='value(name)' | head -n1
}

make_channel() {  # display name, type, label (key=value)
  local existing
  existing="$(channel_named "$1")"
  if [[ -n "$existing" ]]; then echo "$existing"; return; fi
  gc beta monitoring channels create --display-name="$1" --type="$2" \
    --channel-labels="$3" --format='value(name)'
}

echo "== Notification channels"
EMAIL_CHANNEL="$(make_channel "Game platform: email" email "email_address=$ALERT_EMAIL")"
echo "email:     $EMAIL_CHANNEL"
MAJOR="$EMAIL_CHANNEL"
if [[ -n "${PAGERDUTY_KEY:-}" ]]; then
  # The channel type and label are those of the Monitoring API's PagerDuty descriptor;
  # check them with: gcloud beta monitoring channel-descriptors list
  PAGER_CHANNEL="$(make_channel "Game platform: PagerDuty" pagerduty "service_key=$PAGERDUTY_KEY")"
  echo "pagerduty: $PAGER_CHANNEL"; MAJOR="$MAJOR,$PAGER_CHANNEL"
fi
if [[ -n "${SMS_NUMBER:-}" ]]; then
  SMS_CHANNEL="$(make_channel "Game platform: text message" sms "number=$SMS_NUMBER")"
  echo "sms:       $SMS_CHANNEL  (if it shows as unverified, verify it in Monitoring > Alerting > Edit notification channels)"
  MAJOR="$MAJOR,$SMS_CHANNEL"
fi
if [[ -n "${MAJOR_EXTRA_CHANNELS:-}" ]]; then MAJOR="$MAJOR,$MAJOR_EXTRA_CHANNELS"; fi
if [[ "$MAJOR" == "$EMAIL_CHANNEL" ]]; then
  echo "WARNING: no phone, text or PagerDuty channel given, so major alerts only send email." >&2
fi

echo "== Uptime check of https://$HOST/health"
CHECK_NAME="Game platform: $HOST health"
CHECK="$(gc monitoring uptime list-configs --filter="displayName=\"$CHECK_NAME\"" --format='value(name)' | head -n1)"
if [[ -z "$CHECK" ]]; then
  gc monitoring uptime create "$CHECK_NAME" --resource-type=uptime-url \
    --resource-labels="host=$HOST,project_id=$PROJECT_ID" \
    --protocol=https --port=443 --path=/health --status-codes=200 \
    --period=1 --timeout=10 --validate-ssl=true
  CHECK="$(gc monitoring uptime list-configs --filter="displayName=\"$CHECK_NAME\"" --format='value(name)' | head -n1)"
fi
CHECK_ID="${CHECK##*/}"
echo "check id:  $CHECK_ID"

echo "== Alert policies"
python3 "$HERE/policies.py" --minor-channels "$EMAIL_CHANNEL" --major-channels "$MAJOR" \
  --uptime-check-id "$CHECK_ID" --host "$HOST" --out-dir "$OUT" > /dev/null
for file in "$OUT"/*.json; do
  name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["displayName"])' "$file")"
  if [[ -n "$(gc monitoring policies list --filter="displayName=\"$name\"" --format='value(name)')" ]]; then
    echo "exists:    $name"
  else
    gc monitoring policies create --policy-from-file="$file" --format='value(name)' >/dev/null
    echo "created:   $name"
  fi
done
echo
echo "Done. Check it works: docs/monitoring.md, \"Check that the alerts work\"."

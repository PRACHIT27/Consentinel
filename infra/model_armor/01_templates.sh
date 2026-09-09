#!/usr/bin/env bash
# WU-29 — the three Model Armor templates, and why they differ.
#
#   bash infra/model_armor/01_templates.sh
#
# Idempotent: an existing template is left alone rather than replaced, so
# running this twice is safe and running it after someone tuned a template by
# hand does not undo their work.
#
# The asymmetry is the design (DESIGN.md Part III §3). Model Armor's own
# enforcement is only half of it — `consentinel/model_armor.py` decides what a
# match *means*, and that depends entirely on which direction the text is
# travelling:
#
#   consentinel-triage-in   page text  -> triage    FLAG, never block
#   consentinel-ingest-in   contracts  -> ingest    FLAG
#   consentinel-notice-out  draft      -> a human   BLOCK
#
# A page trying to manipulate us is frequently the very page that is
# infringing, so blocking inbound text would suppress the finding we went
# looking for. A takedown notice is the opposite: it must never carry someone's
# personal data or a link to a malware site.
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-consentinel}"
LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

echo "project=$PROJECT location=$LOCATION"
gcloud services enable modelarmor.googleapis.com --project="$PROJECT"

create() {
  local name="$1"; shift
  if gcloud model-armor templates describe "$name" \
       --location="$LOCATION" --project="$PROJECT" >/dev/null 2>&1; then
    echo "  exists, leaving alone: $name"
    return 0
  fi
  echo "  creating: $name"
  gcloud model-armor templates create "$name" \
    --location="$LOCATION" --project="$PROJECT" "$@"
}

# Inbound: page text. Everything on, because we want to know everything a page
# tried — the badge is the product feature. Enforcement stays at inspect-only;
# our code never blocks on an inbound match.
create consentinel-triage-in \
  --pi-and-jailbreak-filter-settings-enforcement=inspect_only \
  --pi-and-jailbreak-filter-settings-confidence-level=low_and_above \
  --malicious-uri-filter-settings-enforcement=enabled \
  --basic-config-filter-enforcement=enabled

# Inbound: contracts and invoices. Personal data is the concern here; a
# contract legitimately contains names, so this labels rather than blocks.
create consentinel-ingest-in \
  --pi-and-jailbreak-filter-settings-enforcement=inspect_only \
  --pi-and-jailbreak-filter-settings-confidence-level=medium_and_above \
  --basic-config-filter-enforcement=enabled

# Outbound: the draft notice. This is the one that blocks.
create consentinel-notice-out \
  --malicious-uri-filter-settings-enforcement=enabled \
  --basic-config-filter-enforcement=enabled \
  --pi-and-jailbreak-filter-settings-enforcement=inspect_only

echo
echo "done. verify with:"
echo "  gcloud model-armor templates list --location=$LOCATION --project=$PROJECT"
echo
echo "Then confirm the app is using them rather than the harness no-op:"
echo "  python -c \"from consentinel.model_armor import describe; print(describe())\""
echo
echo "NOTE: flag names on the templates create command have moved between"
echo "gcloud releases. If one is rejected, run"
echo "  gcloud model-armor templates create --help"
echo "and fix it here rather than in the Python — the module only needs the"
echo "template to exist under these three names."

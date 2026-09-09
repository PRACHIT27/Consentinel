#!/usr/bin/env bash
# Six service accounts, each holding only what it needs.
#
# The point of all of this is one property: no principal in the system can
# delete evidence. Not the agents, not the web app, not a stolen token. That is
# a claim a compliance product can actually make, and it costs about ten minutes.
#
# Note which account ends up weakest: cn-triage, the one component that reads
# attacker-controlled web pages. No secrets, no storage, no database writes, no
# tools. That is the design, not an oversight.
#
#   bash infra/iam/01_service_accounts.sh
set -euo pipefail

PROJECT="${PROJECT:-consentinel}"
MODEL_ROLE="projects/${PROJECT}/roles/consentinelModelInvoker"

sa() { echo "$1@${PROJECT}.iam.gserviceaccount.com"; }

make_sa() {
  local name="$1" desc="$2"
  if gcloud iam service-accounts describe "$(sa "$name")" --project="$PROJECT" >/dev/null 2>&1; then
    echo "  exists   $name"
  else
    gcloud iam service-accounts create "$name" --project="$PROJECT" \
      --display-name="Consentinel ${name#consentinel-}" --description="$desc" >/dev/null
    echo "  created  $name"
  fi
}

grant() {
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$(sa "$1")" --role="$2" --condition=None >/dev/null 2>&1
  echo "      + $2"
}

echo "service accounts"
make_sa consentinel-web        "Cloud Run: reads the registry, invokes the agents. No secrets, no evidence writes."
make_sa consentinel-enforcement "Sweeps the web, decides, drafts. The only account that may write evidence."
make_sa consentinel-triage     "Reads hostile pages. No secrets, no storage, no writes, no tools."
make_sa consentinel-clearance  "Checks our own footage."
make_sa consentinel-ingest     "Reads contracts into permission slips."
make_sa consentinel-scheduler  "Triggers due sweeps. Nothing else."

echo
echo "consentinel-web  (reads, and today also runs consent_ingest in-process)"
grant consentinel-web  roles/datastore.user
grant consentinel-web  "$MODEL_ROLE"

echo "consentinel-enforcement"
grant consentinel-enforcement roles/datastore.user
grant consentinel-enforcement "$MODEL_ROLE"

echo "consentinel-triage  (deliberately the weakest account here)"
grant consentinel-triage "$MODEL_ROLE"
grant consentinel-triage roles/datastore.viewer     # read only. it must never write.

echo "consentinel-clearance"
grant consentinel-clearance roles/datastore.user
grant consentinel-clearance "$MODEL_ROLE"

echo "consentinel-ingest"
grant consentinel-ingest roles/datastore.user
grant consentinel-ingest "$MODEL_ROLE"

echo
echo "consentinel-scheduler gets run.invoker on the service only, not project-wide:"
gcloud run services add-iam-policy-binding consentinel-web \
  --region=us-central1 --project="$PROJECT" \
  --member="serviceAccount:$(sa consentinel-scheduler)" \
  --role=roles/run.invoker >/dev/null 2>&1 && echo "      + roles/run.invoker on consentinel-web"

echo
echo "done. bucket permissions are in 02_buckets.sh - that is where the"
echo "no-one-can-delete-evidence property actually comes from."

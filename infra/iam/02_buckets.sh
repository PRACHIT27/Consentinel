#!/usr/bin/env bash
# Three buckets with three different sets of rules, and the permissions that
# make evidence undeletable.
#
#   evidence/   snapshots taken at discovery. Versioned, retained, and NOBODY
#               held on write, so no one can delete them - not a project owner,
#               An infringing page comes down the moment a notice is sent, so
#               evidence that could be deleted or expire would be worthless
#               exactly when it is needed.
#   uploads/    contracts and clips people give us. Normal.
#   derived/    small copies, transcripts, page text. All regenerable, so it is
#               safe to expire and cheap to lose.
#
# What actually stops a delete: a DEFAULT EVENT-BASED HOLD on the bucket. Every
# new object arrives with a hold on it, and a held object cannot be deleted,
# overwritten or archived by anyone - including a project owner - until the hold
# is released.
#
# Tested, because the first attempt did not work. A bucket retention period was
# set (1 day) and a project owner could still delete a fresh object; the objects
# were not picking up a retention expiry at all. Holds do work, and the refusal
# is explicit:
#
#   403: Object is under active Event-Based hold and cannot be deleted,
#        overwritten or archived until hold is removed
#
# Holds are also the reversible choice. LOCKING a retention policy is permanent:
# it cannot be shortened or removed, and the bucket cannot be deleted until every
# object has aged out. A hold can be released deliberately, which is what you
# want while still building.
#
#   bash infra/iam/02_buckets.sh
set -euo pipefail

PROJECT="${PROJECT:-consentinel}"
REGION="${REGION:-us-central1}"
RETAIN="${RETAIN:-1d}"   # short on purpose while we are still building

EVIDENCE="gs://${PROJECT}-evidence"
UPLOADS="gs://${PROJECT}-uploads"
DERIVED="gs://${PROJECT}-derived"

sa() { echo "serviceAccount:$1@${PROJECT}.iam.gserviceaccount.com"; }

make_bucket() {
  if gcloud storage buckets describe "$1" --project="$PROJECT" >/dev/null 2>&1; then
    echo "  exists   $1"
  else
    gcloud storage buckets create "$1" --project="$PROJECT" --location="$REGION" \
      --uniform-bucket-level-access >/dev/null
    echo "  created  $1"
  fi
}

echo "buckets"
make_bucket "$EVIDENCE"
make_bucket "$UPLOADS"
make_bucket "$DERIVED"

echo
echo "evidence: versioning on, default hold on every new object"
gcloud storage buckets update "$EVIDENCE" --versioning --project="$PROJECT" >/dev/null
gcloud storage buckets update "$EVIDENCE" --retention-period="$RETAIN" --project="$PROJECT" >/dev/null
# The line that does the work. Without it a project owner can delete evidence.
gcloud storage buckets update "$EVIDENCE" --default-event-based-hold --project="$PROJECT" >/dev/null

echo
echo "evidence permissions - this is where the property comes from"
# objectCreator can add an object and cannot read, overwrite or delete one.
gcloud storage buckets add-iam-policy-binding "$EVIDENCE" --project="$PROJECT" \
  --member="$(sa consentinel-enforcement)" --role=roles/storage.objectCreator >/dev/null
echo "      enforcement  objectCreator   (write once; cannot delete or overwrite)"
gcloud storage buckets add-iam-policy-binding "$EVIDENCE" --project="$PROJECT" \
  --member="$(sa consentinel-web)" --role=roles/storage.objectViewer >/dev/null
echo "      web          objectViewer    (read only)"
echo "      triage       nothing         (it reads hostile pages; it gets no storage at all)"

echo
echo "uploads: clearance and ingest read, web writes what people upload"
gcloud storage buckets add-iam-policy-binding "$UPLOADS" --project="$PROJECT" \
  --member="$(sa consentinel-web)" --role=roles/storage.objectAdmin >/dev/null
for s in consentinel-clearance consentinel-ingest; do
  gcloud storage buckets add-iam-policy-binding "$UPLOADS" --project="$PROJECT" \
    --member="$(sa "$s")" --role=roles/storage.objectViewer >/dev/null
done
echo "      web objectAdmin; clearance and ingest objectViewer"

echo
echo "derived: regenerable, so the agents may write and expire freely"
for s in consentinel-enforcement consentinel-clearance; do
  gcloud storage buckets add-iam-policy-binding "$DERIVED" --project="$PROJECT" \
    --member="$(sa "$s")" --role=roles/storage.objectAdmin >/dev/null
done
printf '{"rule":[{"action":{"type":"Delete"},"condition":{"age":7}}]}' > /tmp/cn_lifecycle.json
gcloud storage buckets update "$DERIVED" --lifecycle-file=/tmp/cn_lifecycle.json --project="$PROJECT" >/dev/null
rm -f /tmp/cn_lifecycle.json
echo "      enforcement and clearance objectAdmin; objects expire after 7 days"

echo
echo "proving it: write an object, then try to delete it as whoever you are"
printf 'hold test' > /tmp/cn_hold_test.txt
gcloud storage cp /tmp/cn_hold_test.txt "$EVIDENCE/_selftest/hold.txt" --project="$PROJECT" >/dev/null 2>&1
if gcloud storage rm "$EVIDENCE/_selftest/hold.txt" --project="$PROJECT" >/dev/null 2>&1; then
  echo "      FAILED - the object was deleted. Evidence is NOT protected."
  echo "      Check that --default-event-based-hold applied to the bucket."
else
  echo "      refused, as it should be. Evidence cannot be deleted by anyone."
  gcloud storage objects update "$EVIDENCE/_selftest/hold.txt" --no-event-based-hold --project="$PROJECT" >/dev/null 2>&1
  gcloud storage rm "$EVIDENCE/_selftest/hold.txt" --project="$PROJECT" >/dev/null 2>&1
fi
rm -f /tmp/cn_hold_test.txt

echo
echo "note: IAM alone does not give you this. The bucket still carries legacy"
echo "projectOwner and projectEditor bindings that include object deletion, so"
echo "the hold is what actually stops a delete - not the objectCreator grant."

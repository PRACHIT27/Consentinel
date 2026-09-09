#!/usr/bin/env bash
# Let Firestore delete expired cache entries for us.
#
# The app already refuses to serve an entry past its time, so this is not about
# correctness - it is about not accumulating dead documents forever. A TTL
# policy is a policy, not a cron job we have to write and monitor.
#
# Only documents that HAVE an expires_at are touched. Content-addressed entries
# store it as null on purpose, which is what makes them live forever: the hash
# is the key, so the answer cannot go stale.
#
#   bash infra/firestore/01_cache_ttl.sh
set -euo pipefail
PROJECT="${PROJECT:-consentinel}"

gcloud firestore fields ttls update expires_at \
  --collection-group=cache \
  --enable-ttl \
  --project="$PROJECT" \
  --async

echo "TTL policy requested on cache.expires_at (takes a few minutes to apply)"
echo "check with:"
echo "  gcloud firestore fields ttls list --project=$PROJECT"

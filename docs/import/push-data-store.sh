#!/usr/bin/env bash
# Publish the Data-store storefront (S110) to a running instance via IMPORT.
#
# Why a bundle and not a single posts import: a dataset catalogue page is a
# *widget-driven* page — it renders a `DatasetCatalogue` vue-component widget
# placed into a layout area. That requires THREE entity types, imported in
# dependency order:
#
#   1. cms_widgets  — the DatasetCatalogue / DatasetDetail widget records
#   2. cms_layouts  — dataset-catalogue-layout / dataset-detail-layout; the
#                     layout envelope CARRIES its widget placements
#                     (`widget_assignments`, resolved by widget slug — so the
#                     widgets must exist first)
#   3. cms_posts    — the data-store / dataset-detail pages, each bound to its
#                     layout by slug
#
# Importing only the posts leaves an empty page frame with no catalogue in it.
# All three imports are idempotent upserts (keyed by slug), so re-running is safe.
#
# Seeding (`plugins/dataset/populate_db.py`) is for INITIAL install only; every
# subsequent propagation between instances goes through this import.
#
# Shared nav widgets referenced by the layouts (header-nav, footer-nav,
# breadcrumbs) are intentionally NOT in this bundle — they are platform-wide and
# already exist on any instance; their placements resolve by slug, and a missing
# one safe-degrades (the placement is skipped, the page still imports).
#
# Usage:
#   ./push-data-store.sh <base-url> <admin-email> <admin-password>
# Example:
#   ./push-data-store.sh https://vbwd.cc admin@example.com 'AdminPass123@'
#   ./push-data-store.sh http://localhost:8081 admin@example.com 'AdminPass123@'
#
# Requires: curl, jq, python3
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "Usage: $0 <base-url> <admin-email> <admin-password>" >&2
  exit 2
fi

BASE_URL="${1%/}"
EMAIL="$2"
PASSWORD="$3"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Dependency order: widgets -> layouts -> posts.
ENTITIES=(cms_widgets cms_layouts cms_posts)

echo "=> logging in as $EMAIL at $BASE_URL"
LOGIN=$(curl -sS -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg e "$EMAIL" --arg p "$PASSWORD" '{email:$e,password:$p}')")
TOKEN=$(echo "$LOGIN" | jq -r '.token // .access_token // empty')
if [ -z "$TOKEN" ]; then
  echo "error: login failed — response was:" >&2
  echo "$LOGIN" >&2
  exit 1
fi

for entity in "${ENTITIES[@]}"; do
  file="${HERE}/${entity}.json"
  echo "=> importing ${entity} (${file##*/})"
  RESULT=$(python3 -c "import json,sys;print(json.dumps({'payload':json.load(open(sys.argv[1])),'mode':'upsert','dry_run':False}))" "$file" \
    | curl -sS -X POST "$BASE_URL/api/v1/admin/data-exchange/${entity}/import" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        --data-binary @-)
  echo "   $(echo "$RESULT" | jq -c '{created,updated,skipped,errors}')"
done

echo "=> done. Browse the storefront at: $BASE_URL  ->  /data-store"

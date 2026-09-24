#!/usr/bin/env bash
# cwv-audit stage 3: prove a fix worked.
#
# Pushes the working theme to a NEW UNPUBLISHED theme, re-measures the same URLs
# with the same harness, and diffs against the baseline. Nothing is published and
# the live theme is never touched.
#
# Usage:
#   verify.sh BASELINE_DIR STORE THEME_DIR PATH [PATH...]
#
#   BASELINE_DIR  stage 1 output directory the fix is being compared against
#   STORE         myshopify.com domain
#   THEME_DIR     local theme working copy to push
#   PATH          storefront paths to re-measure, e.g. / /collections/all
#
# Example:
#   bash verify.sh ./out/baseline store.myshopify.com ./theme / /products/foo
#
# Guardrails, deliberately not configurable:
# - --allow-live is never passed. This script cannot publish.
# - The push is confirmed interactively unless CWV_ASSUME_YES=1.
# - The same RUNS and browser as the baseline are reused, read from scan-meta.json.
#   Comparing a 3-run baseline to a 1-run candidate is how a fix gets invented.
set -uo pipefail

if [ "$#" -lt 4 ]; then
  echo "usage: verify.sh BASELINE_DIR STORE THEME_DIR PATH [PATH...]" >&2
  exit 64
fi

BASELINE="$1"; shift
STORE="$1"; shift
THEME_DIR="$1"; shift
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -d "$BASELINE" ]; then
  echo "FATAL baseline directory not found: $BASELINE" >&2
  echo "Run stage 1 first so there is something to compare against." >&2
  exit 66
fi
if [ ! -f "$BASELINE/scan-meta.json" ]; then
  echo "FATAL $BASELINE has no scan-meta.json; it was not produced by scan.sh" >&2
  exit 66
fi
if [ ! -d "$THEME_DIR" ]; then
  echo "FATAL theme directory not found: $THEME_DIR" >&2
  exit 66
fi

# Reuse the baseline harness exactly. A different run count or form factor makes
# the comparison meaningless.
BASE_RUNS=$(sed -n 's/.*"runs_per_url": *\([0-9]*\).*/\1/p' "$BASELINE/scan-meta.json" | head -1)
BASE_FF=$(sed -n 's/.*"form_factor": *"\([a-z]*\)".*/\1/p' "$BASELINE/scan-meta.json" | head -1)
BASE_RUNS="${BASE_RUNS:-3}"
BASE_FF="${BASE_FF:-mobile}"

echo "Baseline: $BASELINE ($BASE_RUNS runs/URL, $BASE_FF)" >&2
echo "Store:    $STORE" >&2
echo "Theme:    $THEME_DIR" >&2
echo >&2
echo "This will push $THEME_DIR to a NEW UNPUBLISHED theme on $STORE." >&2
echo "The live theme is not modified and nothing is published." >&2

if [ "${CWV_ASSUME_YES:-0}" != "1" ]; then
  printf 'Proceed? [y/N] ' >&2
  read -r reply
  case "$reply" in
    y|Y|yes|YES) ;;
    *) echo "Aborted." >&2; exit 75 ;;
  esac
fi

PUSH_JSON="$BASELINE/../push-$(date +%Y%m%d-%H%M%S).json"
echo "Pushing unpublished theme..." >&2
if ! shopify theme push \
      --path "$THEME_DIR" \
      --store "$STORE" \
      --unpublished \
      --theme "cwv-audit candidate $(date +%Y-%m-%d\ %H:%M)" \
      --json > "$PUSH_JSON" 2>"$PUSH_JSON.err"; then
  echo "FATAL theme push failed. stderr:" >&2
  tail -20 "$PUSH_JSON.err" >&2
  exit 70
fi

THEME_ID=$(sed -n 's/.*"id": *\([0-9]*\).*/\1/p' "$PUSH_JSON" | head -1)
if [ -z "$THEME_ID" ]; then
  echo "FATAL could not parse a theme id out of $PUSH_JSON" >&2
  head -20 "$PUSH_JSON" >&2
  exit 70
fi
echo "Pushed unpublished theme id=$THEME_ID" >&2

# Build preview URLs.
#
# The storefront host is taken from the BASELINE, not from the .myshopify.com
# domain. A store with a custom domain measured as https://shop.com/ in the
# baseline would otherwise be re-measured as https://shop.myshopify.com/, and the
# before/after comparison silently matches nothing.
#
# Using the custom domain directly is also the shorter path: Shopify 301s
# myshopify.com -> the primary domain carrying preview_theme_id, then 302s to the
# clean URL while moving the preview into a _shopify_essential cookie. Starting
# on the primary domain skips a hop and ends at the same place. Verified
# 2026-08-09: a marker in the candidate theme is served on the preview URL and
# absent on live, so the cookie handoff does carry the preview.
BASE_HOST=$(awk -F'\t' 'NR==1{print $2}' "$BASELINE/urls.tsv" 2>/dev/null \
            | sed -E 's~^(https?://[^/]+).*~\1~')
STOREFRONT="${CWV_STOREFRONT:-${BASE_HOST:-https://$STORE}}"
echo "Storefront for preview URLs: $STOREFRONT (from baseline)" >&2
CANDIDATE="$BASELINE-candidate-$THEME_ID"
urls=""
for p in "$@"; do
  case "$p" in
    /*) full="$STOREFRONT$p" ;;
    http://*|https://*) full="$p" ;;
    *) full="$STOREFRONT/$p" ;;
  esac
  case "$full" in
    *\?*) urls="$urls $full&preview_theme_id=$THEME_ID" ;;
    *)    urls="$urls $full?preview_theme_id=$THEME_ID" ;;
  esac
done

echo "Re-measuring against preview..." >&2
# shellcheck disable=SC2086  # word splitting is the point: urls is a URL list
RUNS="$BASE_RUNS" FORM_FACTOR="$BASE_FF" bash "$HERE/scan.sh" "$CANDIDATE" $urls
rc=$?
if [ $rc -ne 0 ]; then
  echo "FATAL candidate scan failed (exit $rc). Theme $THEME_ID is still on the store;" >&2
  echo "delete it from Online Store > Themes when you are done." >&2
  exit $rc
fi

echo >&2
python3 "$HERE/merge_findings.py" "$CANDIDATE" --compare "$BASELINE"
merge_rc=$?
echo >&2
echo "Unpublished theme $THEME_ID remains on $STORE. Delete it when done:" >&2
echo "  shopify theme delete --store $STORE --theme $THEME_ID" >&2
if [ $merge_rc -ne 0 ]; then
  echo "FAILED: the comparison did not produce a usable before/after (exit $merge_rc)." >&2
  exit $merge_rc
fi

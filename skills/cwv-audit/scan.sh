#!/usr/bin/env bash
# cwv-audit lab capture: runs Lighthouse N times per URL and writes raw JSON
# into an output directory. Stage 1 of the pipeline.
#
# Usage:
#   scan.sh OUTDIR URL [URL...]
#   RUNS=5 scan.sh OUTDIR URL [URL...]        # more runs, tighter noise band
#   FORM_FACTOR=desktop scan.sh OUTDIR URL    # default is mobile
#   CHROME_PATH=/path/to/chrome scan.sh ...   # override browser autodetect
#
# Output files (N = 1-based URL index, R = 1-based run index):
#   OUTDIR/lh-N-R-<slug>.json   Lighthouse performance category, one per run
#   OUTDIR/urls.tsv             index of N <TAB> URL
#   OUTDIR/scan-meta.json       harness settings, for reproducibility
#   OUTDIR/scan-errors.log      stderr from every failed invocation
#
# Why this is shaped the way it is:
# - Multiple runs, never one. A single Lighthouse run varies by roughly 5-10
#   points. merge_findings.py reports the median AND the spread so a "fix"
#   inside the noise band cannot be claimed as an improvement.
# - Chrome for Testing, never the operator's daily Chrome. A browser carrying
#   extensions and a warm profile cache produces numbers that mean nothing.
# - A warm-up fetch before measuring. Shopify page and section caching makes a
#   cold first hit unrepresentative of what a real visitor gets.
# - npx --yes with a pinned major so a scanner bump can't silently move results.
set -uo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: scan.sh OUTDIR URL [URL...]" >&2
  exit 64
fi

OUTDIR="$1"; shift
RUNS="${RUNS:-3}"
FORM_FACTOR="${FORM_FACTOR:-mobile}"
LH_MAJOR="${LH_MAJOR:-13}"

mkdir -p "$OUTDIR"
: > "$OUTDIR/urls.tsv"

# --- browser selection -------------------------------------------------------
# Prefer an isolated Chrome for Testing build. Fall back to system Chrome only
# with a loud warning, because its extensions and profile cache skew results.
if [ -z "${CHROME_PATH:-}" ]; then
  # shellcheck disable=SC2140  # mixed quoting is deliberate: the mac_arm-* glob
  # must expand while the spaces in the .app path stay quoted.
  for candidate in \
    "$HOME"/.cache/puppeteer/chrome/mac_arm-*/chrome-mac-arm64/"Google Chrome for Testing.app"/Contents/MacOS/"Google Chrome for Testing" \
    "$HOME"/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/"Google Chrome for Testing.app"/Contents/MacOS/"Google Chrome for Testing" \
    "$HOME"/.cache/puppeteer/chrome/mac-*/chrome-mac-x64/"Google Chrome for Testing.app"/Contents/MacOS/"Google Chrome for Testing" \
    "$HOME"/.cache/puppeteer/chrome/linux-*/chrome-linux64/chrome \
    "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux/chrome
  do
    [ -x "$candidate" ] && CHROME_PATH="$candidate"
  done
fi
if [ -z "${CHROME_PATH:-}" ]; then
  if [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
    CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    echo "WARN no Chrome for Testing found; falling back to system Chrome." >&2
    echo "WARN extensions and a warm profile cache will skew these numbers." >&2
    echo "WARN install one with: npx --yes @puppeteer/browsers install chrome@stable" >&2
  else
    echo "FATAL no Chrome binary found. Set CHROME_PATH explicitly." >&2
    exit 69
  fi
fi
export CHROME_PATH

# A dedicated user-data-dir keeps every run from inheriting profile state.
PROFILE_DIR="$OUTDIR/.chrome-profile"
rm -rf "$PROFILE_DIR"
CHROME_FLAGS="--headless=new --no-first-run --no-default-browser-check --user-data-dir=$PROFILE_DIR"

# Plain string, not an array: macOS ships bash 3.2, where expanding an empty
# array under `set -u` is an unbound-variable error. The value is a fixed
# literal with no whitespace, so unquoted expansion is safe here.
PRESET_ARGS=""
if [ "$FORM_FACTOR" = "desktop" ]; then
  PRESET_ARGS="--preset=desktop"
fi

cat > "$OUTDIR/scan-meta.json" <<META
{
  "runs_per_url": $RUNS,
  "form_factor": "$FORM_FACTOR",
  "lighthouse_major": "$LH_MAJOR",
  "chrome_path": "$CHROME_PATH",
  "isolated_profile": true,
  "warmed_before_measuring": true
}
META

echo "Harness: lighthouse@$LH_MAJOR, $RUNS runs/URL, $FORM_FACTOR" >&2
echo "Browser: $CHROME_PATH" >&2

i=0
for url in "$@"; do
  # Only http(s). Anything else, including a dash-prefixed string from a hostile
  # sitemap, would be parsed as a CLI flag by lighthouse.
  case "$url" in
    http://*|https://*) ;;
    *) echo "SKIP non-http(s) argument: $url" >&2; continue ;;
  esac
  i=$((i+1))
  slug=$(echo "$url" | sed -E 's~https?://~~; s~[^A-Za-z0-9]+~-~g; s~-+$~~' | cut -c1-60)
  printf '%s\t%s\n' "$i" "$url" >> "$OUTDIR/urls.tsv"

  # Warm-up. Non-fatal: a failure here only means run 1 may be cache-cold, and
  # the median across runs still protects the result.
  if ! curl -sS -o /dev/null --max-time 45 "$url" 2>> "$OUTDIR/scan-errors.log"; then
    echo "  WARN warm-up fetch failed for $url; run 1 may be cache-cold" >&2
  fi

  r=0
  while [ "$r" -lt "$RUNS" ]; do
    r=$((r+1))
    echo "[$i/$#] run $r/$RUNS: $url" >&2
    # shellcheck disable=SC2086  # PRESET_ARGS must stay unquoted; see note above
    npx --yes "lighthouse@$LH_MAJOR" "$url" \
      --only-categories=performance \
      $PRESET_ARGS \
      --output=json --output-path="$OUTDIR/lh-$i-$r-$slug.json" \
      --chrome-flags="$CHROME_FLAGS" \
      --max-wait-for-load=60000 \
      --quiet 2>> "$OUTDIR/scan-errors.log" \
      || echo "  WARN lighthouse run $r failed for $url (see scan-errors.log)" >&2
  done
done

rm -rf "$PROFILE_DIR"

# A scan that produced nothing must not look like a scan that found no problems.
#
# Count only USABLE reports. Lighthouse writes a full ~140KB JSON even when the
# run failed, embedding a runtimeError (e.g. CHROME_INTERSTITIAL_ERROR for an
# unreachable host). Counting files instead of successes reports "6/6" for a
# scan where half the runs failed.
# Counted with an explicit loop, not `grep -L`: combining -L and -c is ambiguous
# and its precedence differs between BSD and GNU grep, which produced a "6/3"
# count on a 3-file directory during testing.
produced=0
for f in "$OUTDIR"/lh-*.json; do
  [ -f "$f" ] || continue
  grep -q '"runtimeError"' "$f" || produced=$((produced+1))
done
expected=$((i * RUNS))
echo "Scan complete: $produced/$expected usable Lighthouse reports in $OUTDIR" >&2
if [ "$produced" -eq 0 ]; then
  echo "FATAL no Lighthouse reports were produced. See $OUTDIR/scan-errors.log" >&2
  exit 70
fi
if [ "$produced" -lt "$expected" ]; then
  echo "WARN $((expected - produced)) run(s) failed; merge will mark affected URLs UNSCANNED" >&2
fi

# Record how fast this host was. Lighthouse measures environment.benchmarkIndex
# every run and warns below 1000: a slow or busy machine stacks on top of the
# simulated 4x CPU throttle and drags TBT, LCP and the score down. Keeping it in
# scan-meta.json makes a baseline taken on a loaded laptop visible later.
python3 - "$OUTDIR" <<'PY' || echo "WARN could not record benchmark_index in scan-meta.json" >&2
import glob, json, os, statistics, sys
out = sys.argv[1]
vals = []
for p in glob.glob(os.path.join(out, "lh-*.json")):
    try:
        with open(p) as fh:
            doc = json.load(fh)
    except Exception:
        continue
    if doc.get("runtimeError"):
        continue
    b = (doc.get("environment") or {}).get("benchmarkIndex")
    if isinstance(b, (int, float)):
        vals.append(b)
meta_path = os.path.join(out, "scan-meta.json")
with open(meta_path) as fh:
    meta = json.load(fh)
if vals:
    meta["benchmark_index"] = {"median": round(statistics.median(vals)),
                               "min": round(min(vals)), "max": round(max(vals))}
    if min(vals) < 1000:
        print(f"WARN slow host: benchmarkIndex {round(min(vals))} < 1000; "
              "results read worse than a real device", file=sys.stderr)
with open(meta_path, "w") as fh:
    json.dump(meta, fh, indent=2)
PY
echo "Next: python3 merge_findings.py $OUTDIR" >&2

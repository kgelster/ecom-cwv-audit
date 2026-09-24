#!/usr/bin/env python3
"""cwv-audit stage 0: field-data triage.

Field data decides WHAT to fix. Lab data explains WHY. Running stage 1 without
this means optimizing whatever Lighthouse happens to complain about, which is
not the same as what is costing the merchant money.

Primary source is the CrUX API. Shopify's own RUM (per-template, better data)
is attempted opportunistically and reported on when unreachable, because it sits
behind an access gate most tokens do not clear. See references/field-data.md.

Stdlib only. No pip installs.

Usage:
  python3 field.py --origin https://example.com --out OUTDIR
  python3 field.py --origin https://example.com --url https://example.com/products/x --out OUTDIR
  python3 field.py --origin https://example.com --shop store.myshopify.com --out OUTDIR
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

CRUX_ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
UA = "cwv-audit/1.0"

# Google's Core Web Vitals thresholds. good <= GOOD, poor > POOR, else needs-improvement.
THRESHOLDS = {
    "largest_contentful_paint": (2500, 4000, "ms", "LCP"),
    "interaction_to_next_paint": (200, 500, "ms", "INP"),
    "cumulative_layout_shift": (0.10, 0.25, "", "CLS"),
    "first_contentful_paint": (1800, 3000, "ms", "FCP"),
    "experimental_time_to_first_byte": (800, 1800, "ms", "TTFB"),
}

# LCP subparts, field-side. These are the same four phases the DevTools
# Performance panel shows, which means LCP can be attributed to a phase from
# real-user data and not only from a lab trace.
LCP_SUBPARTS = [
    "largest_contentful_paint_image_time_to_first_byte",
    "largest_contentful_paint_image_resource_load_delay",
    "largest_contentful_paint_image_resource_load_duration",
    "largest_contentful_paint_image_element_render_delay",
]

CORE_METRICS = list(THRESHOLDS.keys()) + LCP_SUBPARTS + ["largest_contentful_paint_resource_type"]


def rate(metric: str, p75) -> str:
    if metric not in THRESHOLDS or p75 is None:
        return "n/a"
    good, poor, _, _ = THRESHOLDS[metric]
    try:
        v = float(p75)
    except (TypeError, ValueError):
        return "n/a"
    if v <= good:
        return "good"
    if v > poor:
        return "poor"
    return "needs-improvement"


def crux_query(key: str, form_factor: str, *, origin=None, url=None) -> dict:
    """Query CrUX. Returns {'status': ..., ...}.

    A 404 means CrUX has no sample for this target. That is 'insufficient data',
    never 'good' -- a low-traffic page silently reported as passing is the single
    most dangerous failure mode in this stage.
    """
    body = {"formFactor": form_factor, "metrics": CORE_METRICS}
    if origin:
        body["origin"] = origin
    else:
        body["url"] = url
    target = origin or url

    req = urllib.request.Request(
        f"{CRUX_ENDPOINT}?key={key}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        if exc.code == 404:
            return {"status": "insufficient-data", "target": target, "detail":
                    "CrUX has no sample for this target. Not a pass."}
        if exc.code == 403:
            return {"status": "forbidden", "target": target, "detail":
                    "403 from CrUX. Key is missing, invalid, or the Chrome UX Report API "
                    "is not enabled on the project. " + raw[:300]}
        if exc.code == 429:
            return {"status": "rate-limited", "target": target, "detail":
                    "429 from CrUX (150 queries/minute/project)."}
        return {"status": "error", "target": target, "detail": f"HTTP {exc.code}: {raw[:300]}"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "target": target, "detail": f"{type(exc).__name__}: {exc}"}

    record = payload.get("record") or {}
    metrics = record.get("metrics") or {}
    out = {
        "status": "ok",
        "target": target,
        "form_factor": form_factor,
        "collection_period": record.get("collectionPeriod"),
        "metrics": {},
        "lcp_subparts": {},
    }
    for name, node in metrics.items():
        p75 = (node.get("percentiles") or {}).get("p75")
        entry = {"p75": p75}
        if name in THRESHOLDS:
            entry["rating"] = rate(name, p75)
            entry["label"] = THRESHOLDS[name][3]
            out["metrics"][name] = entry
        elif name in LCP_SUBPARTS:
            out["lcp_subparts"][name] = p75
        elif name == "largest_contentful_paint_resource_type":
            out["lcp_resource_type"] = node.get("fractions")
    return out


def shopify_rum_probe(shop: str, token: str) -> dict:
    """Attempt Shopify's own RUM via shopifyqlQuery.

    Shopify's per-template RUM beats CrUX for a store: it is segmented by page
    type rather than sampled per URL. But shopifyqlQuery requires the
    `read_reports` scope AND Level 2 protected customer data approval. Most
    custom-app tokens have neither. Report the gate precisely instead of
    returning empty and letting the caller assume there is no data.
    """
    gql = (
        "query Q($q:String!){ shopifyqlQuery(query:$q){ parseErrors "
        "tableData{ columns{ name dataType displayName } rows } } }"
    )
    req = urllib.request.Request(
        f"https://{shop}/admin/api/2026-07/graphql.json",
        data=json.dumps({"query": gql, "variables": {"q": "FROM sales SHOW total_sales SINCE -7d"}}).encode(),
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            payload = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}

    errors = payload.get("errors") or []
    for err in errors:
        code = (err.get("extensions") or {}).get("code")
        if code == "ACCESS_DENIED":
            return {
                "status": "access-denied",
                "detail": err.get("message", "")[:400],
                "remedy": (
                    "shopifyqlQuery needs the `read_reports` access scope plus Level 2 "
                    "protected customer data approval. Add read_reports to the custom app, "
                    "reinstall it, and request Level 2 data access. Until then this stage "
                    "runs on CrUX only."
                ),
            }
    if errors:
        return {"status": "graphql-error", "detail": json.dumps(errors)[:400]}
    return {"status": "ok", "detail": "shopifyqlQuery reachable. Per-template RUM available."}


def fmt(metric: str, entry: dict) -> str:
    p75 = entry.get("p75")
    unit = THRESHOLDS.get(metric, (0, 0, "", ""))[2]
    label = entry.get("label", metric)
    val = f"{p75}{unit}" if p75 is not None else "-"
    return f"{label} p75={val} [{entry.get('rating', 'n/a')}]"


def main() -> int:
    ap = argparse.ArgumentParser(description="cwv-audit stage 0: field triage")
    ap.add_argument("--origin", help="origin to query, e.g. https://example.com")
    ap.add_argument("--url", action="append", default=[], help="specific page URL (repeatable)")
    ap.add_argument("--form-factor", default="PHONE", choices=["PHONE", "TABLET", "DESKTOP"])
    ap.add_argument("--shop", help="myshopify.com domain, to probe Shopify RUM availability")
    ap.add_argument("--token-env", default="", help="env var holding the shop's Admin API token")
    ap.add_argument("--out", required=True, help="output directory")
    args = ap.parse_args()

    if not args.origin and not args.url:
        print("FATAL need --origin or at least one --url", file=sys.stderr)
        return 64

    key = os.environ.get("CRUX_API_KEY", "").strip()
    if not key:
        print(
            "FATAL no CRUX_API_KEY in the environment.\n"
            "\n"
            "Field data is not optional for this skill: without it you cannot tell which\n"
            "page is actually costing money, and you cannot measure INP at all (Lighthouse\n"
            "reports TBT as a lab proxy, which is not INP).\n"
            "\n"
            "The keyless PageSpeed Insights endpoint is not a workaround; it returns 429\n"
            "almost immediately from a normal IP.\n"
            "\n"
            "Get a key (free, no billing, 150 queries/minute):\n"
            "  1. https://console.cloud.google.com/apis/credentials -> Create API key\n"
            "  2. Enable 'Chrome UX Report API' on the same project\n"
            "  3. export CRUX_API_KEY=... in the shell that runs this\n"
            "\n"
            "To run lab-only anyway, skip this stage and call scan.sh directly. The report\n"
            "must then state that findings are unprioritized and INP is Undetermined.",
            file=sys.stderr,
        )
        return 78

    os.makedirs(args.out, exist_ok=True)
    result = {"form_factor": args.form_factor, "origin": None, "pages": [], "shopify_rum": None}

    if args.origin:
        print(f"CrUX origin: {args.origin} [{args.form_factor}]", file=sys.stderr)
        result["origin"] = crux_query(key, args.form_factor, origin=args.origin)
    for url in args.url:
        print(f"CrUX page:   {url}", file=sys.stderr)
        result["pages"].append(crux_query(key, args.form_factor, url=url))

    if args.shop:
        token = os.environ.get(args.token_env, "") if args.token_env else ""
        if not token:
            result["shopify_rum"] = {
                "status": "no-token",
                "detail": f"--shop given but no token in env var {args.token_env or '(unset --token-env)'}",
            }
        else:
            result["shopify_rum"] = shopify_rum_probe(args.shop, token)

    path = os.path.join(args.out, "field.json")
    with open(path, "w") as fh:
        json.dump(result, fh, indent=2)

    # Human summary
    lines = ["", "=== FIELD DATA (CrUX, 28-day rolling p75) ==="]
    targets = ([("origin", result["origin"])] if result["origin"] else []) + [
        ("page", p) for p in result["pages"]
    ]
    ranked = []
    for kind, node in targets:
        if not node:
            continue
        if node["status"] != "ok":
            lines.append(f"[{kind}] {node.get('target')}: {node['status'].upper()} - {node.get('detail', '')}")
            continue
        head = f"[{kind}] {node['target']}"
        metrics = node.get("metrics", {})
        parts = [fmt(m, e) for m, e in metrics.items() if m in ("largest_contentful_paint",
                                                               "interaction_to_next_paint",
                                                               "cumulative_layout_shift")]
        lines.append(f"{head}\n    " + "  ".join(parts))
        sub = node.get("lcp_subparts") or {}
        if sub:
            total = sum(v for v in sub.values() if isinstance(v, (int, float))) or 1
            frag = ", ".join(
                f"{n.replace('largest_contentful_paint_image_', '')}={v}ms ({round(100 * v / total)}%)"
                for n, v in sub.items() if isinstance(v, (int, float))
            )
            lines.append(f"    LCP phases: {frag}")
        for m, e in metrics.items():
            if e.get("rating") in ("poor", "needs-improvement"):
                ranked.append((node["target"], e.get("label", m), e.get("p75"), e["rating"]))

    if result.get("shopify_rum"):
        s = result["shopify_rum"]
        lines += ["", f"=== SHOPIFY RUM === {s['status'].upper()}", f"    {s.get('detail', '')}"]
        if s.get("remedy"):
            lines.append(f"    remedy: {s['remedy']}")

    lines += ["", "=== WHAT TO FIX FIRST ==="]
    if ranked:
        order = {"poor": 0, "needs-improvement": 1}
        for tgt, label, p75, rating in sorted(ranked, key=lambda r: order.get(r[3], 2)):
            lines.append(f"  {rating.upper():<18} {label:<5} {p75:<8} {tgt}")
    else:
        lines.append("  Nothing rated poor or needs-improvement in the targets queried.")
        lines.append("  This is not the same as 'the site is fast'. Targets returning")
        lines.append("  insufficient-data above are Undetermined, not passing.")
    lines += ["", f"Wrote {path}", "Next: bash scan.sh OUTDIR <the URLs ranked above>", ""]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())

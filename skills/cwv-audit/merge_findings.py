#!/usr/bin/env python3
"""cwv-audit stage 2: compress raw Lighthouse output into findings a model can read.

One Lighthouse run against a real storefront is ~1.6MB of JSON. A three-URL,
three-run scan is ~15MB. Reading that into a context window is both wasteful and
worse than useless, because the signal is a few dozen numbers.

This script reduces it to findings.json plus summary.md, ranked by estimated
milliseconds saved, with every third-party cost attributed to the app that owns
it. The model reads those two files and nothing else.

Written against Lighthouse 13 INSIGHT audits. Lighthouse 13 removed the legacy
audit ids (offscreen-images, uses-rel-preload, render-blocking-resources, ...)
from the report AND from the JSON, so a parser written from memory silently
finds nothing and reports a clean site. Every id below was read out of a real
13.4.1 report, not recalled.

Stdlib only. No pip installs.

Usage:
  python3 merge_findings.py OUTDIR
  python3 merge_findings.py CANDIDATE_DIR --compare BASELINE_DIR
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys

MAX_NODES = 5          # sample instances per finding; the rest are counted, not listed
MAX_THIRD_PARTIES = 12

HERE = os.path.dirname(os.path.abspath(__file__))

# Lab metrics worth tracking. INP is deliberately absent: Lighthouse in
# navigation mode does not emit interaction-to-next-paint at all. TBT is a lab
# proxy for responsiveness, and it is not INP.
METRICS = {
    "largest-contentful-paint": ("LCP", "ms", 2500, 4000),
    "cumulative-layout-shift": ("CLS", "", 0.10, 0.25),
    "total-blocking-time": ("TBT", "ms", 200, 600),
    "first-contentful-paint": ("FCP", "ms", 1800, 3000),
    "speed-index": ("SI", "ms", 3400, 5800),
    "server-response-time": ("TTFB", "ms", 800, 1800),
}

SEVERITY_MS = ((500, "P0"), (150, "P1"))   # by estimated savings


def rate(label: str, value) -> str:
    for _, (lbl, _u, good, poor) in METRICS.items():
        if lbl == label and value is not None:
            return "good" if value <= good else ("poor" if value > poor else "needs-improvement")
    return "n/a"


def load_apps() -> list:
    path = os.path.join(HERE, "shopify-apps.json")
    try:
        with open(path) as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN could not load shopify-apps.json ({exc}); attribution disabled", file=sys.stderr)
        return []
    rows = []
    for section in ("first_party", "apps", "analytics_and_payment"):
        rows.extend(data.get(section) or [])
    # Longest match first so 'klaviyo-media.com' beats 'klaviyo.com'.
    rows.sort(key=lambda r: max((len(m) for m in r.get("match", [])), default=0), reverse=True)
    return rows


def attribute(urls: list, entity: str, apps: list) -> dict:
    """Attribute a third-party group to a Shopify app.

    Match on request hostname, not on the entity name. Lighthouse's entity
    names come from third-party-web and are sometimes wrong for Shopify
    ecosystem scripts: cdn.ywxi.net (TrustedSite) reports as 'Tencent'.
    """
    blob = " ".join(urls).lower()
    for row in apps:
        for needle in row.get("match", []):
            if needle.lower() in blob:
                return {
                    "app": row.get("app") or row.get("entity"),
                    "owner": row.get("owner", "unknown"),
                    "surface": row.get("surface", "none"),
                    "route": row.get("route", ""),
                    "perf_notes": row.get("perf_notes", ""),
                    "matched_on": needle,
                }
    return {
        "app": entity or "unidentified third party",
        "owner": "unknown",
        "surface": "none",
        "route": "Not in shopify-apps.json. Identify the vendor from the request hostname "
                 "before recommending a fix, and add it to the table once identified.",
        "perf_notes": "",
        "matched_on": None,
    }


# --- insight extractors ------------------------------------------------------
# Each returns (headline, [evidence strings]).

def _nodes(items, key="node"):
    out = []
    for it in items or []:
        node = it.get(key) if isinstance(it, dict) else None
        if isinstance(node, dict) and node.get("type") == "node":
            out.append(node)
    return out


def ex_lcp_breakdown(audit):
    items = (audit.get("details") or {}).get("items") or []
    phases, element = [], None
    for it in items:
        if it.get("type") == "table":
            for row in it.get("items") or []:
                if "subpart" in row:
                    phases.append((row.get("label", row["subpart"]), row.get("duration")))
        elif it.get("type") == "node":
            element = it
    total = sum(d for _, d in phases if isinstance(d, (int, float))) or 1
    ev = [f"{lbl}: {round(d)}ms ({round(100 * d / total)}%)" for lbl, d in phases
          if isinstance(d, (int, float))]
    if element:
        ev.append(f"LCP element: {element.get('selector')}")
        if element.get("nodeLabel"):
            ev.append(f"  label: {str(element['nodeLabel'])[:120]}")
    worst = max(((lbl, d) for lbl, d in phases if isinstance(d, (int, float))),
                key=lambda p: p[1], default=(None, 0))
    head = f"LCP dominated by {worst[0]} ({round(worst[1])}ms)" if worst[0] else "LCP breakdown"
    return head, ev


def ex_cls_culprits(audit):
    """Details is a list of tables, one per shift cluster.

    The same element usually appears in several clusters, so collapse by
    selector and keep its worst score. Listing 'div#main' three times reads as
    three problems when it is one.
    """
    items = (audit.get("details") or {}).get("items") or []
    worst, by_selector = 0.0, {}
    for tbl in items:
        for row in (tbl.get("items") or []):
            node, score = row.get("node"), row.get("score")
            if isinstance(score, (int, float)):
                worst = max(worst, score)
            if isinstance(node, dict) and node.get("type") == "node":
                sel = node.get("selector") or "(unknown)"
                s = score if isinstance(score, (int, float)) else 0.0
                by_selector[sel] = max(by_selector.get(sel, 0.0), s)
    ranked = sorted(by_selector.items(), key=lambda kv: -kv[1])[:MAX_NODES]
    ev = [f"{round(s, 3)} <- {sel}" for sel, s in ranked]
    return f"Layout shift, worst cluster {round(worst, 3)}", ev


def ex_checklist(audit):
    items = (audit.get("details") or {}).get("items") or {}
    failed = [v.get("label", k) for k, v in items.items()
              if isinstance(v, dict) and v.get("value") is False]
    passed = [v.get("label", k) for k, v in items.items()
              if isinstance(v, dict) and v.get("value") is True]
    head = failed[0] if failed else "Document latency checks passed"
    return head, [f"FAIL {f}" for f in failed] + [f"ok   {p}" for p in passed]


def _flatten_rows(det):
    """Return data rows from a details blob.

    Insight details come as type=table (rows directly) or type=list (rows nested
    one level down inside sub-tables). Without the list case, forced-reflow and
    network-dependency-tree render as empty findings.
    """
    items = det.get("items") or []
    if det.get("type") != "list":
        return items
    rows = []
    for sub in items:
        if isinstance(sub, dict) and sub.get("type") == "table":
            rows.extend(sub.get("items") or [])
        elif isinstance(sub, dict) and sub.get("type") == "node":
            rows.append({"node": sub})
    return rows


def ex_table(audit, cols=("url", "wastedBytes", "wastedMs", "totalBytes")):
    det = audit.get("details") or {}
    rows = _flatten_rows(det)
    ev = []
    for row in rows[:MAX_NODES]:
        bits = []
        node = row.get("node")
        if isinstance(node, dict) and node.get("selector"):
            bits.append(str(node["selector"])[:90])
        for c in cols:
            v = row.get(c)
            if isinstance(v, (int, float)) and v:
                unit = "KB" if "Bytes" in c else "ms"
                val = round(v / 1024) if "Bytes" in c else round(v)
                bits.append(f"{c}={val}{unit}")
            elif c == "url" and row.get("url"):
                bits.append(str(row["url"])[:110])
        if bits:
            ev.append(" | ".join(bits))
    extra = len(rows) - MAX_NODES
    if extra > 0:
        ev.append(f"... and {extra} more")
    return audit.get("title", "issue"), ev


EXTRACTORS = {
    "lcp-breakdown-insight": ex_lcp_breakdown,
    "cls-culprits-insight": ex_cls_culprits,
    "document-latency-insight": ex_checklist,
}


def extract(audit_id, audit):
    fn = EXTRACTORS.get(audit_id)
    if fn:
        return fn(audit)
    return ex_table(audit)


def savings_ms(audit) -> float:
    ms = audit.get("metricSavings") or {}
    vals = [v for v in ms.values() if isinstance(v, (int, float))]
    return float(max(vals)) if vals else 0.0


def severity(sv: float, score) -> str:
    for threshold, sev in SEVERITY_MS:
        if sv >= threshold:
            return sev
    return "P2" if (score is not None and score < 1) else "P3"


# --- per-URL aggregation -----------------------------------------------------

def read_reports(outdir: str):
    """Group Lighthouse reports by URL index. Returns (per_url, unscanned)."""
    urls = {}
    tsv = os.path.join(outdir, "urls.tsv")
    if os.path.exists(tsv):
        with open(tsv) as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 2:
                    urls[parts[0]] = parts[1]

    grouped = {}
    for path in sorted(glob.glob(os.path.join(outdir, "lh-*.json"))):
        base = os.path.basename(path)[3:]           # strip 'lh-'
        idx = base.split("-", 1)[0]
        try:
            with open(path) as fh:
                doc = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN unreadable {base}: {exc}", file=sys.stderr)
            continue
        if doc.get("runtimeError"):
            print(f"WARN {base} has runtimeError: "
                  f"{(doc['runtimeError'] or {}).get('code')}", file=sys.stderr)
            continue
        grouped.setdefault(idx, []).append(doc)

    unscanned = [(i, u) for i, u in urls.items() if i not in grouped]
    return urls, grouped, unscanned


def summarise_url(url, docs):
    """Median + spread per metric, plus findings from the highest-scoring run."""
    out = {"url": url, "runs": len(docs), "metrics": {}, "findings": []}

    scores = [(d.get("categories", {}).get("performance") or {}).get("score")
              for d in docs]
    scores = [round(s * 100) for s in scores if isinstance(s, (int, float))]
    if scores:
        out["performance_score"] = {
            "median": round(statistics.median(scores)),
            "min": min(scores), "max": max(scores),
            "spread": max(scores) - min(scores),
        }

    for audit_id, (label, unit, _g, _p) in METRICS.items():
        vals = []
        for d in docs:
            a = (d.get("audits") or {}).get(audit_id) or {}
            v = a.get("numericValue")
            if isinstance(v, (int, float)):
                vals.append(v)
        if not vals:
            continue
        med = statistics.median(vals)
        out["metrics"][label] = {
            "median": round(med, 3) if unit == "" else round(med),
            "min": round(min(vals), 3) if unit == "" else round(min(vals)),
            "max": round(max(vals), 3) if unit == "" else round(max(vals)),
            "spread": round(max(vals) - min(vals), 3) if unit == "" else round(max(vals) - min(vals)),
            "unit": unit,
            "rating": rate(label, med),
        }

    # INP is structurally unavailable here. Say so rather than omitting it,
    # because an omitted metric reads as a passing metric.
    has_inp = any(((d.get("audits") or {}).get("interaction-to-next-paint") or {}).get("numericValue")
                  is not None for d in docs)
    out["inp"] = {
        "available": has_inp,
        "note": "" if has_inp else
                "Lighthouse navigation mode does not measure INP. TBT is a lab proxy, not INP. "
                "INP is Undetermined from this scan: get it from field data (field.py) or from "
                "a scripted interaction trace via chrome-devtools-mcp.",
    }

    # Findings come from the median-scoring run so evidence matches the numbers.
    ranked_docs = sorted(
        docs, key=lambda d: (d.get("categories", {}).get("performance") or {}).get("score") or 0)
    ref = ranked_docs[len(ranked_docs) // 2]
    apps = load_apps()

    for audit_id, audit in sorted((ref.get("audits") or {}).items()):
        if not audit_id.endswith("-insight"):
            continue
        score = audit.get("score")
        sv = savings_ms(audit)
        det = audit.get("details") or {}
        if det.get("type") is None and score is None:
            continue                       # e.g. inp-breakdown-insight with no interactions

        # third-parties-insight scores 1 with no metricSavings even when it has
        # found seconds of third-party main-thread work. It carries the whole
        # ownership story, so it is never filtered on score.
        if audit_id == "third-parties-insight":
            tp = third_party_finding(audit, apps)
            if tp["third_parties"]:
                out["findings"].append(tp)
            continue

        if score == 1 and sv == 0 and audit_id != "lcp-breakdown-insight":
            continue                       # passing and nothing to save

        head, ev = extract(audit_id, audit)
        # A finding with no savings and no evidence is noise, not a finding.
        if not ev and sv == 0 and audit_id != "lcp-breakdown-insight":
            continue
        out["findings"].append({
            "id": audit_id,
            "title": audit.get("title", audit_id),
            "headline": head,
            "severity": severity(sv, score),
            "estimated_savings_ms": round(sv),
            "score": score,
            "owner": "theme",
            "evidence": ev[:MAX_NODES + 3],
        })

    _check_lcp_consistency(out)
    out["findings"].sort(key=lambda f: (-f.get("estimated_savings_ms", 0), f.get("severity", "P3")))
    return out


def _check_lcp_consistency(out):
    """Flag when the LCP phase breakdown does not add up to the measured LCP.

    lcp-breakdown-insight attributes the phases of one LCP candidate. When the
    page's real LCP lands much later (late-loading hero, a shift that promotes a
    new element), the phases sum to a fraction of the metric. Reporting 'LCP
    dominated by element render delay (296ms)' while LCP is 10968ms would send
    someone to optimize the wrong 5% of the problem.
    """
    lcp = (out.get("metrics") or {}).get("LCP", {}).get("median")
    if not isinstance(lcp, (int, float)):
        return
    for f in out["findings"]:
        if f["id"] != "lcp-breakdown-insight":
            continue
        total = 0.0
        for line in f.get("evidence", []):
            if ":" in line and "ms (" in line:
                try:
                    total += float(line.split(":")[1].strip().split("ms")[0])
                except ValueError:
                    pass
        if total and total < lcp * 0.6:
            f["headline"] = (f"{f['headline']} - but these phases sum to {round(total)}ms "
                             f"against a measured LCP of {round(lcp)}ms")
            f["evidence"].append(
                f"INCONSISTENT: phases account for only {round(100 * total / lcp)}% of LCP. "
                "The breakdown is describing an earlier LCP candidate than the one that won. "
                "Do not optimize these phases; trace the real LCP element with "
                "chrome-devtools-mcp (performance_start_trace, then LCPBreakdown).")
            f["severity"] = "P1"


def third_party_finding(audit, apps):
    rows = (audit.get("details") or {}).get("items") or []
    groups = []
    for row in rows:
        urls = [s.get("url", "") for s in ((row.get("subItems") or {}).get("items") or [])]
        attr = attribute(urls, row.get("entity", ""), apps)
        groups.append({
            "entity": row.get("entity"),
            "app": attr["app"],
            "owner": attr["owner"],
            "surface": attr["surface"],
            "route": attr["route"],
            "perf_notes": attr["perf_notes"],
            "main_thread_ms": round(row.get("mainThreadTime") or 0),
            "transfer_kb": round((row.get("transferSize") or 0) / 1024),
            "sample_urls": urls[:3],
        })
    groups.sort(key=lambda g: -g["main_thread_ms"])
    groups = groups[:MAX_THIRD_PARTIES]
    total = sum(g["main_thread_ms"] for g in groups)
    return {
        "id": "third-parties-insight",
        "title": "Third-party main-thread cost",
        "headline": f"{len(groups)} third parties, {total}ms main thread",
        "severity": "P0" if total >= 1000 else ("P1" if total >= 400 else "P2"),
        "estimated_savings_ms": total,
        "score": audit.get("score"),
        "owner": "mixed",
        "third_parties": groups,
        "evidence": [f"{g['app']} [{g['owner']}]: {g['main_thread_ms']}ms, {g['transfer_kb']}KB"
                     for g in groups],
    }


# --- rendering ---------------------------------------------------------------

def render(result, compare=None) -> str:
    L = ["# Core Web Vitals audit", ""]
    meta = result.get("harness") or {}
    L += [f"Harness: lighthouse@{meta.get('lighthouse_major', '?')}, "
          f"{meta.get('runs_per_url', '?')} runs/URL, {meta.get('form_factor', '?')}, "
          f"isolated Chrome.", ""]

    if result["unscanned"]:
        L += ["## UNSCANNED", "",
              "These URLs produced no usable report. They are Undetermined, not passing.", ""]
        L += [f"- {u}" for _i, u in result["unscanned"]] + [""]

    for page in result["pages"]:
        L += [f"## {page['url']}", ""]
        ps = page.get("performance_score")
        if ps:
            L.append(f"Performance score: **{ps['median']}** "
                     f"(range {ps['min']}-{ps['max']} across {page['runs']} runs, "
                     f"spread {ps['spread']})")
        row = []
        for label, m in page["metrics"].items():
            row.append(f"{label} {m['median']}{m['unit']} [{m['rating']}] ±{m['spread']}{m['unit']}")
        if row:
            L += ["", "```", "\n".join(row), "```"]
        if not page["inp"]["available"]:
            L += ["", f"> INP: **Undetermined.** {page['inp']['note']}"]

        if ps and ps["spread"] >= 5:
            L += ["", f"> Noise band is {ps['spread']} points. Any 'improvement' smaller than "
                      f"that is not measurable with this run count. Raise RUNS to tighten it."]

        L += ["", "### Findings", ""]
        if not page["findings"]:
            L.append("No insight audit reported an opportunity.")
        for f in page["findings"]:
            sv = f.get("estimated_savings_ms", 0)
            L.append(f"**[{f['severity']}] {f['title']}** "
                     f"{'~' + str(sv) + 'ms' if sv else ''} `{f['id']}`")
            L.append(f"  {f['headline']}")
            for e in f.get("evidence", []):
                L.append(f"  - {e}")
            if f.get("third_parties"):
                L.append("")
                L.append("  | app | owner | main thread | transfer | fix route |")
                L.append("  |---|---|---|---|---|")
                for g in f["third_parties"]:
                    route = (g["route"] or "")[:90].replace("|", "/")
                    L.append(f"  | {g['app']} | {g['owner']} | {g['main_thread_ms']}ms "
                             f"| {g['transfer_kb']}KB | {route} |")
            L.append("")

    if compare:
        L += ["", "## Before / after", ""] + compare
    return "\n".join(L)


def compare_dirs(candidate, baseline_dir):
    base_meta = os.path.join(baseline_dir, "scan-meta.json")
    if not os.path.exists(base_meta):
        return ["Baseline has no scan-meta.json; refusing to compare."], 0
    b_urls, b_grouped, _ = read_reports(baseline_dir)
    base = {}
    for idx, docs in b_grouped.items():
        base[b_urls.get(idx, idx)] = summarise_url(b_urls.get(idx, idx), docs)

    lines, matched = [], 0
    for page in candidate["pages"]:
        # Preview URLs carry ?preview_theme_id=; strip it to match the baseline.
        key = page["url"].split("preview_theme_id=")[0].rstrip("?&")
        match = None
        for burl, bpage in base.items():
            if burl.rstrip("/") == key.rstrip("/"):
                match = bpage
                break
        if not match:
            lines.append(f"- {page['url']}: **no baseline counterpart, SKIPPED** "
                         f"(baseline has: {', '.join(sorted(base)) or 'nothing'})")
            continue
        matched += 1
        # A preview URL pays for an extra redirect: Shopify 302s it to the clean
        # URL while moving the preview into a _shopify_essential cookie. That
        # inflates TTFB against a baseline measured on a direct hit, so a TTFB
        # "regression" here is an artifact of the harness, not of the theme.
        is_preview = "preview_theme_id=" in page["url"]
        lines.append(f"### {key}")
        for label, m in page["metrics"].items():
            bm = match["metrics"].get(label)
            if not bm:
                continue
            delta = m["median"] - bm["median"]
            band = max(m["spread"], bm["spread"])
            if abs(delta) <= band:
                verdict = f"within noise (±{band}{m['unit']})"
            else:
                verdict = "IMPROVED" if delta < 0 else "REGRESSED"
            if is_preview and label == "TTFB" and verdict == "REGRESSED":
                verdict = ("REGRESSED - but not comparable: the preview URL adds a "
                           "redirect the baseline did not pay for. Re-measure TTFB "
                           "after publishing, or compare preview against preview.")
            sign = "+" if delta > 0 else ""
            lines.append(f"- {label}: {bm['median']}{m['unit']} -> {m['median']}{m['unit']} "
                         f"({sign}{round(delta, 3)}) {verdict}")
        bs, cs = match.get("performance_score"), page.get("performance_score")
        if bs and cs:
            d = cs["median"] - bs["median"]
            band = max(bs["spread"], cs["spread"])
            verdict = (f"within noise (±{band})" if abs(d) <= band
                       else ("IMPROVED" if d > 0 else "REGRESSED"))
            lines.append(f"- score: {bs['median']} -> {cs['median']} ({'+' if d > 0 else ''}{d}) {verdict}")
        lines.append("")
    return lines, matched


def main() -> int:
    ap = argparse.ArgumentParser(description="cwv-audit stage 2: merge and attribute")
    ap.add_argument("outdir")
    ap.add_argument("--compare", help="baseline directory to diff against")
    args = ap.parse_args()

    if not os.path.isdir(args.outdir):
        print(f"FATAL not a directory: {args.outdir}", file=sys.stderr)
        return 66

    urls, grouped, unscanned = read_reports(args.outdir)
    if not grouped:
        print(f"FATAL no usable Lighthouse reports in {args.outdir}.\n"
              f"An empty scan is not a clean site. Check scan-errors.log.", file=sys.stderr)
        return 70

    harness = {}
    meta_path = os.path.join(args.outdir, "scan-meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as fh:
                harness = json.load(fh)
        except Exception:  # noqa: BLE001
            pass

    result = {"harness": harness, "pages": [], "unscanned": unscanned}
    for idx in sorted(grouped, key=lambda k: int(k) if k.isdigit() else 0):
        result["pages"].append(summarise_url(urls.get(idx, f"url-{idx}"), grouped[idx]))

    cmp_lines, cmp_matched = (compare_dirs(result, args.compare)
                              if args.compare else (None, None))

    fj = os.path.join(args.outdir, "findings.json")
    with open(fj, "w") as fh:
        json.dump(result, fh, indent=2)
    sm = os.path.join(args.outdir, "summary.md")
    text = render(result, cmp_lines)
    with open(sm, "w") as fh:
        fh.write(text)

    raw = sum(os.path.getsize(p) for p in glob.glob(os.path.join(args.outdir, "lh-*.json")))
    print(f"Compressed {round(raw / 1024 / 1024, 1)}MB raw -> "
          f"{round(os.path.getsize(sm) / 1024)}KB summary.md, "
          f"{round(os.path.getsize(fj) / 1024)}KB findings.json", file=sys.stderr)
    if unscanned:
        print(f"WARN {len(unscanned)} URL(s) UNSCANNED and reported as Undetermined", file=sys.stderr)
    print(f"Read {sm} and {fj}. Do not read the lh-*.json files.", file=sys.stderr)

    # A comparison that matched nothing produced no evidence either way. Exiting
    # 0 here lets "no regression" be read out of a run that compared nothing at
    # all, which is the worst possible failure for a verification step.
    if args.compare and not cmp_matched:
        print("\nFATAL --compare matched 0 pages against the baseline. No before/after\n"
              "conclusion can be drawn from this run. Usual cause: the candidate was\n"
              "measured on a different host than the baseline (myshopify.com vs the\n"
              "primary domain). Re-run with matching hosts.", file=sys.stderr)
        return 65
    return 0


if __name__ == "__main__":
    sys.exit(main())

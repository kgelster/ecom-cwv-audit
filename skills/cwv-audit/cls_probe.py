#!/usr/bin/env python3
"""cwv-audit: measure CLS the way a real visitor produces it, with scrolling.

Why this exists. Lighthouse runs headless and never scrolls, so lazy-injected
content -- ad slots above all -- never mounts and never shifts. On an ad-supported
page its CLS is systematically optimistic. Measured on an ad-supported content
store 2026-08-09: Lighthouse said CLS 0.024 and reported ZERO shift culprits, while CrUX
field data said 0.17.

This probe drives a real browser, scrolls the page in steps, and records every
layout-shift entry with the elements responsible, so a field CLS number can be
attributed to actual DOM.

CLS is computed the way Google computes it: the largest session window, where a
window ends after a 1s gap between shifts or 5s of total duration. Summing every
shift instead would overstate a long page badly.

Requires playwright (pip install playwright). Usage:
  python3 cls_probe.py https://example.com --out OUTDIR
  python3 cls_probe.py https://example.com --out OUTDIR --desktop --no-scroll
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("playwright not installed. pip install playwright && playwright install chromium")

# Installed before any page script runs, so no shift is missed. Buffered entries
# alone are not enough: the observer must be live for the whole scroll.
OBSERVER = """
window.__cls = { entries: [] };
function sel(n) {
  if (!n || n.nodeType !== 1) return null;
  const parts = [];
  let el = n, depth = 0;
  while (el && el.nodeType === 1 && depth < 4) {
    let s = el.tagName.toLowerCase();
    if (el.id) { parts.unshift(s + '#' + el.id); break; }
    if (el.className && typeof el.className === 'string') {
      const c = el.className.trim().split(/\\s+/).slice(0, 2).join('.');
      if (c) s += '.' + c;
    }
    parts.unshift(s);
    el = el.parentElement; depth++;
  }
  return parts.join(' > ');
}
new PerformanceObserver((list) => {
  for (const e of list.getEntries()) {
    if (e.hadRecentInput) continue;   // user-initiated shifts do not count toward CLS
    window.__cls.entries.push({
      value: e.value,
      startTime: e.startTime,
      sources: (e.sources || []).map(s => ({
        selector: sel(s.node),
        node: s.node ? (s.node.outerHTML || '').slice(0, 160) : null,
        from: s.previousRect ? {y: s.previousRect.y, h: s.previousRect.height} : null,
        to: s.currentRect ? {y: s.currentRect.y, h: s.currentRect.height} : null,
      })),
    });
  }
}).observe({ type: 'layout-shift', buffered: true });
"""


def session_windows(entries):
    """Largest CLS session window: 1s gap between shifts, 5s max window length."""
    best, cur, start, last = 0.0, 0.0, None, None
    best_entries, cur_entries = [], []
    for e in sorted(entries, key=lambda x: x["startTime"]):
        t = e["startTime"]
        if start is None or t - last > 1000 or t - start > 5000:
            if cur > best:
                best, best_entries = cur, list(cur_entries)
            cur, cur_entries, start = 0.0, [], t
        cur += e["value"]
        cur_entries.append(e)
        last = t
    if cur > best:
        best, best_entries = cur, list(cur_entries)
    return best, best_entries


def find_chrome() -> str | None:
    """Reuse the same isolated Chrome that scan.sh picks.

    The pip playwright package pins a chromium build that is often not the one in
    the local cache, and downloading another ~150MB Chrome to measure the same
    page two ways is waste. Sharing the binary also keeps cls_probe comparable
    with the Lighthouse runs, which matters when both numbers land in one report.
    """
    import glob as _glob
    pats = [
        os.path.expanduser("~/.cache/puppeteer/chrome/mac_arm-*/chrome-mac-arm64/"
                           "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
        os.path.expanduser("~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/"
                           "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
        os.path.expanduser("~/.cache/puppeteer/chrome/mac-*/chrome-mac-x64/"
                           "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
        os.path.expanduser("~/.cache/puppeteer/chrome/linux-*/chrome-linux64/chrome"),
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    env = os.environ.get("CHROME_PATH")
    if env and os.access(env, os.X_OK):
        return env
    for pat in pats:
        hits = sorted(_glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure CLS with scrolling")
    ap.add_argument("url")
    ap.add_argument("--out", required=True)
    ap.add_argument("--desktop", action="store_true")
    ap.add_argument("--no-scroll", action="store_true",
                    help="reproduce Lighthouse's no-scroll behaviour, for comparison")
    ap.add_argument("--steps", type=int, default=12)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    viewport = {"width": 1366, "height": 900} if args.desktop else {"width": 412, "height": 823}
    with sync_playwright() as pw:
        chrome = find_chrome()
        if not chrome:
            sys.exit("no Chrome binary found; set CHROME_PATH or run: playwright install chromium")
        print(f"browser: {chrome}", file=sys.stderr)
        browser = pw.chromium.launch(headless=True, executable_path=chrome)
        ctx = browser.new_context(
            viewport=viewport,
            is_mobile=not args.desktop,
            has_touch=not args.desktop,
            device_scale_factor=2 if not args.desktop else 1,
            user_agent=None if args.desktop else
            ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36"),
        )
        page = ctx.new_page()
        page.add_init_script(OBSERVER)
        page.goto(args.url, wait_until="domcontentloaded", timeout=90000)
        try:
            page.wait_for_load_state("networkidle", timeout=25000)
        except Exception:  # noqa: BLE001
            pass

        if not args.no_scroll:
            # Step down the page, pausing so lazy/ad content can mount and shift.
            for i in range(args.steps):
                page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {(i + 1) / args.steps})")
                page.wait_for_timeout(900)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(1500)

        data = page.evaluate("window.__cls")
        browser.close()

    entries = data.get("entries", [])
    cls, window = session_windows(entries)
    total = sum(e["value"] for e in entries)

    by_sel = {}
    for e in window:
        for s in e["sources"] or []:
            k = s.get("selector") or "(unknown)"
            by_sel[k] = by_sel.get(k, 0.0) + e["value"] / max(len(e["sources"] or [1]), 1)

    result = {
        "url": args.url,
        "form_factor": "desktop" if args.desktop else "mobile",
        "scrolled": not args.no_scroll,
        "cls_session_window": round(cls, 4),
        "cls_naive_sum": round(total, 4),
        "shift_count": len(entries),
        "culprits": [{"selector": k, "contribution": round(v, 4)}
                     for k, v in sorted(by_sel.items(), key=lambda kv: -kv[1])],
        "entries": entries[:60],
    }
    path = os.path.join(args.out, "cls-probe.json")
    with open(path, "w") as fh:
        json.dump(result, fh, indent=2)

    rating = "good" if cls <= 0.10 else ("poor" if cls > 0.25 else "needs-improvement")
    print(f"\nCLS (largest session window): {round(cls, 4)} [{rating}]")
    print(f"  naive sum of all shifts:     {round(total, 4)}  across {len(entries)} shifts")
    print(f"  scrolled:                    {not args.no_scroll}")
    print("\nCulprits by contribution:")
    for c in result["culprits"][:12]:
        print(f"  {c['contribution']:>7.4f}  {c['selector']}")
    if not result["culprits"]:
        print("  none recorded")
    print(f"\nWrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

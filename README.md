# ecom-cwv-audit

**A Core Web Vitals audit that fixes what real shoppers experience, and knows which app owns the slow part.**

Point it at a Shopify store. It pulls field data to decide which page is actually costing the merchant, measures that page with multi-run Lighthouse in an isolated browser, attributes every third-party millisecond to the Shopify app that loaded it, and proves a fix on an unpublished theme before anyone publishes anything. Built for Claude Code, and for any agent runtime that reads `SKILL.md` skills.

**Field data decides, lab data explains.** On one ad-supported store, lab and field disagreed in opposite directions on both metrics:

| metric | lab (Lighthouse mobile) | field (CrUX p75) |
|---|---|---|
| LCP | 6586ms **poor** | 1213ms **good** |
| CLS | 0.024 **good** | 0.17 **needs-improvement** |

Working off the PageSpeed report would have spent the whole engagement on an LCP real users never see, and missed the CLS they do. Lighthouse mobile throttles to slow 4G with 4x CPU, so its LCP is pessimistic. It never scrolls, so its CLS is optimistic. This skill runs `field.py` first for that reason.

**Attribution is the other half.** Most of the main-thread cost on a real Shopify store did not come from the theme. The review widget, the upsell engine, the popup, and the chat launcher each ship their own bundle. Telling the merchant to "fix the theme" for those sends them to edit code they don't control. `shopify-apps.json` maps ~55 vendors by request hostname (not by Lighthouse's entity name, which reports TrustedSite as "Tencent") to an owner and a fix route: app settings, or a vendor ticket with the measured cost attached. Unknown hosts are reported as unidentified, never guessed.

**Honesty is the product.** Lighthouse does not measure INP, so the report prints INP as Undetermined rather than leaving it out. A change inside the measured noise band is reported as `within noise`, not as a win. A URL whose scan failed is reported as unscanned, not as clean. A comparison that matched zero pages exits non-zero.

## What it does

1. **Stage 0, field triage.** `field.py` queries the Chrome UX Report API for origin and URL-level p75 LCP, INP, and CLS, plus the field-side LCP phase breakdown. Optional Shopify RUM probe (segmented by page type) where the store's token allows it. `insufficient-data` is Undetermined, never a pass.
2. **Stage 1, lab measurement.** `scan.sh` runs Lighthouse 13 three times per URL (configurable) in an isolated Chrome for Testing build with a throwaway profile, warms each URL first so Shopify's page cache doesn't skew the cold hit, and records the harness in `scan-meta.json`. Default URL set is the three templates Shopify's own speed score weights: product, collection, home.
3. **Stage 2, compress and attribute.** `merge_findings.py` reduces ~15MB of raw Lighthouse JSON to a `summary.md` of roughly 8-20KB and a `findings.json`, ranked by estimated milliseconds saved, with medians and the noise band per metric. Parses the Lighthouse 13 `*-insight` audits plus the three per-script diagnostics that survived the cut (`unused-javascript`, `bootup-time`, `total-byte-weight`), with every script attributed to its owner. Most legacy ids are gone, so a parser written from memory returns nothing and looks like a clean site. Flags an LCP phase breakdown that describes a different element than the one that won. Lists the audits passing in every run as a do-not-regress set, and flags a slow measuring host from Lighthouse's `benchmarkIndex`.
4. **Stage 2b, CLS with scrolling.** `cls_probe.py` drives a real browser, scrolls in steps, and attributes each layout shift to a selector, computing CLS as the largest session window the way Google does. Catches lazy-mounted shifts Lighthouse never triggers.
5. **Stage 3, prove the fix.** `verify.sh` pushes the working theme to a new **unpublished** theme with the Shopify CLI, re-measures the same URLs with the same harness settings read back from the baseline, and diffs. Reports any audit that passed every baseline run and fails every candidate run as NEWLY FAILING. It cannot publish.
6. **Carries the Shopify rules.** `references/theme-rules.md` condenses Shopify's theme performance catalog (Liquid render cost, image filters, stylesheet count, section loading, Theme Check rules). `references/fixes.md` maps each Lighthouse insight to what changes in a Shopify theme.

Every finding is graded twice, P0-P3 for impact and Verified/Flagged/Human-required for evidence, and takes the lower grade.

## Install

As a Claude Code plugin:

```
/plugin marketplace add kgelster/ecom-cwv-audit
/plugin install cwv-audit@kgelster-cwv
```

For Codex, Cursor, or any other agent that reads `SKILL.md` skills, the
[`skills` CLI](https://agentskills.io) installs it straight from this repo:

```
npx skills add https://github.com/kgelster/ecom-cwv-audit --skill cwv-audit
```

## Requirements

- Node 18+ (Lighthouse runs through `npx --yes lighthouse@13`, no global install).
- Python 3.10+. `cls_probe.py` also needs `pip install playwright` (it reuses the Chrome for Testing binary below rather than downloading its own).
- A Chrome for Testing build: `npx --yes @puppeteer/browsers install chrome@stable`. Autodetected on macOS and Linux; override with `CHROME_PATH`. Falls back to system Chrome with a loud warning, because extensions and a warm profile skew the numbers.
- `CRUX_API_KEY` for stage 0: a free Google Cloud API key with the Chrome UX Report API enabled (150 queries/minute, no billing). `field.py` prints the provisioning steps if it's missing.
- Shopify CLI, authenticated to the store, for stage 3 only.

## Pairs with

- [addyosmani/web-quality-skills](https://github.com/addyosmani/web-quality-skills) for generic, platform-independent LCP/INP/CLS fix knowledge. This skill deliberately does not repeat it.
- [chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) for element-level traces, and the only lab path to INP (it can script an interaction).
- [ecom-a11y-audit](https://github.com/kgelster/ecom-a11y-audit) and [ecom-cookie-audit](https://github.com/kgelster/ecom-cookie-audit), same attribution approach for accessibility and consent.

## Known limits

- **Preview-vs-live comparisons overstate regressions.** Shopify injects preview-mode scripts into every preview response, so `verify.sh`'s candidate-preview vs direct-baseline diff can mark untouched pages REGRESSED. The skill documents the control-theme procedure (trap 13 in `SKILL.md`); until `verify.sh` automates it, treat its before/after as indicative.
- **Ad-driven CLS cannot be reproduced locally.** Ad networks don't fill for headless browsers or datacenter IPs. Only the next CrUX window confirms an ad CLS fix, and the skill says so.
- Checkout is Shopify-hosted and out of scope.

## Versioning

Semantic versioning, recorded in `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` and tagged `vX.Y.Z` on GitHub. Every release gets an entry in [`CHANGELOG.md`](CHANGELOG.md). A major bump means a report consumer has to change: a renamed `findings.json` field, a removed stage, or a changed exit code. A minor bump adds findings or checks. A patch fixes behavior without changing the output shape. CI fails when the two manifests and the top changelog entry disagree.

## License

MIT

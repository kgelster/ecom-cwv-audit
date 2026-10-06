# Changelog

All notable changes to this skill. Versions follow [Semantic Versioning](https://semver.org/): see the Versioning section of the README for what counts as major, minor, and patch here.

## [1.2.0] - 2026-10-06

### Changed
- Synced to Shopify's theme performance doc (re-read 2026-10-06). Speed-score weights are now collection 43%, product 40%, home 17%, replacing the old 31/33/13 formula. The Theme Store bar (plain average of 60 or more) is now stated separately.
- `section.index` rules account for the counter restarting in every section group.

### Added
- Stage 0 metric-gap triage (TTFB, FCP minus TTFB, LCP minus FCP) before any lab run.
- Traps 14-16: preview themes are not streamed; on a streamed page TTFB covers only the layout head; Early Hints are missing on the first hit after a publish.
- `theme-rules.md`: HTML streaming and `content_for_header` ordering with the three pre-move checks, `limit` versus `paginate`, guarded dialog Liquid, combined listings, critical API requests before DOM ready, fallback font metrics, system fonts, preconnect and preload budget rules, speculation rules, `ContentForHeaderModification`.
- `fixes.md`: streamed-TTFB reading, `font_face` without `font_display`, `content_for_header` check for render-blocking findings.
- `field-data.md`: unstable `performanceMetrics` / `performanceEvents` Admin API fields; streaming lowers field TTFB on its own.

## [1.1.0] - 2026-09-24

### Added
- `merge_findings.py` parses `unused-javascript`, `bootup-time` and `total-byte-weight`. Lighthouse 13.5 still emits these three legacy diagnostics, and they are the only audits that name individual bundles. Every row is attributed to the app or theme that shipped it.
- Do-not-regress list: each page in `summary.md` lists the scored audits that passed in every run.
- `--compare` reports NEWLY FAILING for an audit that passed every baseline run and fails every candidate run, and NOW PASSING for the reverse. Audits that flip in only some runs count as noise and are not reported.
- Host `benchmarkIndex`: `scan.sh` records it in `scan-meta.json`. `summary.md` prints it and warns below Lighthouse's own 1000 line. `--compare` flags a shift of more than 25% between baseline and candidate.
- `shopify-apps.json` attributes theme and platform assets served from a custom domain (`/cdn/shop/t/`, `/cdn/shop/files/`, `/cdn/shopifycloud/`, `/cdn/wpm/`).
- `CHANGELOG.md`, and a CI check that the plugin manifests and this file agree on the version.

### Changed
- Audit ids re-verified against Lighthouse 13.5.0 (18 insight audits, unchanged set).

## [1.0.0] - 2026-09-24

Initial release: CrUX field triage, multi-run Lighthouse 13 in an isolated browser, third-party cost attributed to the owning Shopify app, scrolling CLS probe, and re-measurement on an unpublished theme.

[1.2.0]: https://github.com/kgelster/ecom-cwv-audit/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/kgelster/ecom-cwv-audit/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/kgelster/ecom-cwv-audit/releases/tag/v1.0.0

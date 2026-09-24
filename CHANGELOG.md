# Changelog

All notable changes to this skill. Versions follow [Semantic Versioning](https://semver.org/): see the Versioning section of the README for what counts as major, minor, and patch here.

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

[1.1.0]: https://github.com/kgelster/ecom-cwv-audit/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/kgelster/ecom-cwv-audit/releases/tag/v1.0.0

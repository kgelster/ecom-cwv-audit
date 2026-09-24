# Insight id to Shopify theme fix

Read at judge time, after `summary.md`. Each entry maps a Lighthouse 13 insight
to what actually changes in a Shopify theme. Generic web guidance lives in
`addyosmani/web-quality-skills`; this file is the Shopify-specific layer.

Shopify's own performance rules that these fixes assume are condensed in
`references/theme-rules.md` (Liquid render cost, image filters, stylesheet
count, section loading, Theme Check rules). Upstream source:
https://shopify.dev/docs/storefronts/themes/best-practices/performance

---

## lcp-breakdown-insight

Four phases. Shopify's target allocation, from the Chrome DevTools LCP skill:
TTFB ~40%, resource load delay <10%, resource load duration ~40%, element render
delay <10%.

- **TTFB high** on Shopify is rarely the theme. Shopify's edge usually returns in
  under 100ms; a slow TTFB means an app doing server-side work, a redirect chain,
  or a Liquid loop over a large collection. Check `document-latency-insight`
  first for redirects.
- **Resource load delay high** means the browser found the LCP image late. The
  image is being set by JS, is inside a lazy-loaded section, or lacks a
  discoverable `src`. Fix: render the hero image server-side in Liquid with a
  real `src`, and `preload` it.
- **Resource load duration high** means the image is too big. Fix: `image_url`
  with an explicit `width`, and `image_tag` for `srcset`.
- **Element render delay high** means the image arrived but could not paint:
  render-blocking CSS/JS, or a web font blocking text. Cross-reference
  `render-blocking-insight` and `font-display-insight`.

**If merge_findings.py flagged this INCONSISTENT**, the phases describe an
earlier LCP candidate than the one that won. Do not act on them. Trace the real
element with chrome-devtools-mcp.

```liquid
{{ section.settings.hero | image_url: width: 1200 | image_tag:
     loading: 'eager', fetchpriority: 'high', preload: true,
     sizes: '100vw', widths: '400, 600, 800, 1200, 1600' }}
```

Never `loading="lazy"` on the LCP element. Never a raw CDN URL without a `width`.

## lcp-discovery-insight

The LCP image is not discoverable by the preload scanner. Same fix as resource
load delay above: server-rendered `src`, `fetchpriority="high"`, `preload`.

Shopify caps useful preloads at about two per template. Preloading everything
means preloading nothing.

## cls-culprits-insight

The reported selector is the shifting element, which is often not the cause. The
cause is usually an element above it that arrived late without reserved space.

Common Shopify sources, in order of frequency:
- App widgets hydrating above the fold (Klaviyo forms, review stars, subscription
  widgets). Reserve height in the theme with `min-height` or an aspect-ratio box.
- Images without `width`/`height`. `image_tag` emits both; raw `<img>` does not.
- Web fonts swapping. See `font-display-insight`.
- Announcement bars and cart drawers that mount after load.
- Collection filter apps re-rendering the grid.

A whole-page shift attributed to `div#main` or `body` means something at the very
top of the document changed height. Look at the announcement bar and the header.

## image-delivery-insight

Almost always theme-owned and almost always the cheapest large win.

- Serve through `image_url` with a `width` that matches the rendered size. A 1000px
  source rendered at 60px is the single most common finding on a Shopify collection
  page.
- Use `image_tag` so `srcset` and `sizes` are emitted.
- Shopify serves WebP automatically to supporting browsers; do not hand-convert.
- Below-the-fold images get `loading: 'lazy'`. Above-the-fold does not.

## render-blocking-insight

- Theme CSS in `<head>` is expected. App CSS in `<head>` usually is not.
- `<script>` tags in sections should be `defer` or `type="module"`.
- Third-party scripts belong at the end of `<body>` or `async`, never blocking in
  `<head>`. If an app injected itself into `theme.liquid`, ownership is the theme
  and it can be moved.
- Never `document.write()`.

## font-display-insight

Set `font-display: swap` on `@font-face`. Shopify's `font_face` filter takes it:

```liquid
{{ settings.type_body_font | font_face: font_display: 'swap' }}
```

Preload only the one or two fonts used above the fold. Self-hosted fonts on the
Shopify CDN beat Google Fonts and Typekit on connection cost.

## third-parties-insight

Not a theme fix. See `references/attribution.md` and route by owner.

If a "speed optimizer" app is installed, treat its reported gains as unproven
until CrUX moves. Shopify documents the fraud patterns (LCP hijacking with a
transparent overlay, `Chrome-Lighthouse` user-agent branching, deferring loads
past the measurement window); the tell is a 20+ point PSI-vs-DevTools gap with
flat field data.

The two questions worth asking about any third party: does it need to load on
*this* template, and does it need to load *before* interaction. Most app widgets
answer no to both.

## document-latency-insight

A checklist. The usual Shopify failure is `noRedirects: false` - a redirect chain
costing 100-300ms on every page load. Causes: a `www` / apex mismatch, a stale
URL redirect, or a locale redirect. Fix at the DNS/domain level, not in the theme.

The other DNS-level cause is a **request proxy** (Cloudflare, Fastly, a WAF)
sitting in front of Shopify and adding a hop to every request. Detection and
the curl/dig commands are in `references/theme-rules.md`. If TTFB is high and
redirects are clean, check this before blaming Liquid.

When Liquid *is* the cause, it is almost always a loop: nested iteration,
metafield access inside a loop, or `product.variants` iterated to build an
option picker. See `references/theme-rules.md`.

## cache-insight

Third-party assets with short `max-age`. Not merchant-fixable for vendor scripts;
report as vendor cost. Theme assets on `cdn.shopify.com` are already cached
aggressively, so a cache finding pointing at the theme usually means an asset is
being served from the app proxy (`/apps/...`) rather than the CDN.

## legacy-javascript-insight / duplicated-javascript-insight

Polyfills and duplicate bundles. On a Shopify store these are nearly always
app-owned. When it is the theme, it is usually a vendored library that the theme
also loads from a CDN.

Shopify's guidance: keep minified theme JS bundles at 16KB or less, avoid
third-party frameworks, prefer native browser features.

## dom-size-insight / slow-css-selector-insight / forced-reflow-insight

Responsiveness, not load. These matter for INP, which this scan cannot measure -
confirm against field data before spending effort here.

Large DOM on a Shopify collection page usually means too many products rendered
per page. `paginate` with a smaller page size.

## modern-http-insight / viewport-insight

`modern-http` is Shopify-controlled for `cdn.shopify.com`; findings here belong
to third-party hosts. `viewport-insight` failing means the viewport meta tag is
missing or malformed in `layout/theme.liquid`, which also breaks mobile
rendering outright.

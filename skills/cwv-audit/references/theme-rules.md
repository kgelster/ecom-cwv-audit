# Shopify theme performance rules

Shopify's own theme performance catalog, condensed. Source:
https://shopify.dev/docs/storefronts/themes/best-practices/performance
(read 2026-08-18, re-read 2026-10-06 for HTML streaming, `content_for_header`
ordering, and the new speed-score weights). Impact labels are Shopify's, not
this skill's.

Read this when a finding is **theme-owned** and you are writing the fix, or when
auditing a theme statically without a scan. `references/fixes.md` maps Lighthouse
insight ids to fixes; this file is the rule catalog behind them. Generic,
platform-independent web guidance is deliberately not repeated here; that layer
is `addyosmani/web-quality-skills`.

**The bar:** Theme Store requires a minimum average Lighthouse **performance
score of 60** and **accessibility score of 90**, each averaged across home,
product, and collection, on both desktop and mobile, measured on Shopify's
benchmark shop. A merchant theme has no such gate, so 60 is a floor for
published themes, not a target for a client engagement.

Shopify's separate **speed score** weights the same three pages: collection 43%,
product 40%, home 17%. Rank work with the weighted score; check the Theme Store
bar with the plain average.

---

## TTFB: Liquid render cost

Liquid runs on Shopify's servers before a single byte reaches the browser. Every
rule here moves TTFB, which no amount of front-end work can recover.

**Nested loops are multiplicative** (high). 50 products x 10 variants is 500
operations, and doubling the catalog roughly quadruples render work. Replace the
inner loop with a filter:

```liquid
{%- comment -%} Bad: O(n x m) {%- endcomment -%}
{% for product in collection.products %}
  {% for item in cart.items %}
    {% if item.variant_id == product.selected_variant.id %}...{% endif %}
  {% endfor %}
{% endfor %}

{%- comment -%} Good: one map, then a contains test {%- endcomment -%}
{% assign cart_variant_ids = cart.items | map: "variant_id" %}
{% for product in collection.products %}
  {% if cart_variant_ids contains product.selected_variant.id %}...{% endif %}
{% endfor %}
```

Same shape with `where` + `first` instead of a filtered inner loop, and
`variant.options | join: " / "` instead of iterating options.

**Metafield access inside a loop queries the backend every iteration** (high).
50 products x 10 variants is 500+ backend queries and seconds of TTFB. Read once
above the loop into a variable.

```liquid
{% assign badge_label = collection.metafields.custom.badge_label %}
{% for product in collection.products %}
  {% if badge_label != blank %}<span class="badge">{{ badge_label }}</span>{% endif %}
{% endfor %}
```

**Never iterate `product.variants` to build an option picker** (high). It forces
the server to load complete data for every variant combination; Shopify allows
up to 2,048. Use `product.options_with_values`, which carries only the option
names and values the picker needs, and update the rest via the Section Rendering
API on change.

**Pagination ceilings** (medium). `paginate` clamps at **250 per page**;
metaobject entries at 1,000. Beyond **25,000 objects** the platform stops
counting and returns `25001`. Deep pagination scans the whole range on every
request. Ship 12-50 products per page and make filters the narrowing mechanism.

**Hoist everything invariant out of loops** (medium/low). `assign` statements,
repeated filter chains, and `block.settings` lookups all re-evaluate per
iteration. Assign once above the loop. Flatten nested `{% render %}` calls
inside loops. Filter the collection *before* entering the loop rather than
`{% if %}`-ing inside it.

**`echo` beats `append`/`prepend`** (low) for string output: no intermediate
string allocation per call.

**`limit` cuts the fetch only for `collection.products` and `collections`**
(high). On those two, `{% for product in collection.products limit: 4 %}` fetches
4 products, not the default 50. On every other array (`blog.articles`,
`search.results`, `product.variants`, `pages`, `customer.orders`,
`article.comments`), `limit` caps iterations only and the full page is still
fetched; wrap it in `{% paginate x by 4 %}` and keep the `limit`. Do not wrap a
fixed carousel of products in `paginate`: it then responds to `?page=2` and
renders the wrong items. Shopify's suggested sizes: featured 4-6, carousels 4-8,
recommendations 2-4, grids 8-12, paginated collections 24-50.

**Guard Liquid inside closed dialogs and drawers** (medium). A predictive-search
or cart drawer that evaluates `collections.all.products` on every page pays that
cost for visitors who never open it. Horizon 2.1.4 saved 400ms per page load by
removing one unguarded `| default: collections.all.products`. On stores with
1,000+ variants per product, `collections.all` without a `limit` adds 1-3s+ of
TTFB. Pass a `load_content` parameter that is `false` in the initial render, and
fetch a dedicated section with `load_content: true` through the Section
Rendering API on first open.

**Combined listings: render the parent's `options_with_values`, defer the
children** (medium). Load child product data through the Section Rendering API
when the shopper picks an option, not in the initial render.

**`section.index` counts per location, not per page** (medium). The counter
restarts at 1 in each section group, the JSON template, and `content_for_index`;
disabled sections take no index. So `section.index == 1` is true for the first
footer section too. Combine it with `section.location` (`template`, `static`,
`content_for_index`, `preset`, `header`, `footer`, `aside`, `custom.<name>`)
when page-level position matters. Only one image per page should get
`fetchpriority: 'high'`: gate it on `section.index == 1` and a template location,
not on index alone.

## FCP: HTML streaming and `content_for_header`

Shopify streams most storefront responses in two parts. Part one is the layout
from `<!doctype html>` to `{{ content_for_header }}`, sent as soon as it renders.
Part two (the `content_for_header` output through `</body>`) follows when the
sections finish. Total Liquid time does not change. What changes is that the
browser fetches everything in part one while the sections are still rendering.

**Eligibility.** The page renders from a JSON template (`.liquid` templates are
not streamed yet), and the layout has `{{ content_for_header }}` as a plain
output tag inside `<head>`. Any filter, `{% if %}`, `{% capture %}`, `{% liquid %}`,
variable assignment, or move into a snippet turns streaming off for every page on
that layout. Preview themes, the preview bar, and the theme editor are never
streamed.

**Load first-paint resources above `content_for_header`** (high, FCP). Above the
tag: charset, viewport, title; `stylesheet_tag` links for base and above-the-fold
styles; `font_face` declarations and one font `preload_tag`; the settings-driven
`{% style %}` block of CSS variables; the import map, `modulepreload` links, and
the module scripts that use them; `async` scripts that fetch above-the-fold data
from an external API. Horizon already does this. Dawn puts `content_for_header`
early and loads `base.css`, fonts, and component CSS below it, so those wait for
the sections. Many themes are Dawn-derived; check this first.

Keep base styles in an `assets/` file, not in `{% stylesheet %}` tags. Shopify
compiles `{% stylesheet %}` output into one file linked from
`content_for_header`, so it always arrives in part two.

**Three checks before moving anything above the tag** (`content_for_header`
defines `window.Shopify` and emits its own CSS and deferred JS):

1. Inline or parser-blocking scripts that read `Shopify.*` throw
   `ReferenceError: Shopify is not defined` above the tag. Leave them, or read the
   value in Liquid (`routes.root_url`, `request.locale`, `cart.currency`,
   `request.design_mode`). The editor defines `Shopify` early, so this only
   breaks on the live storefront. Test outside the editor.
2. Cascade order flips. A theme stylesheet moved above the tag now loses equal-
   specificity ties to the compiled `{% stylesheet %}` file and to the dynamic
   checkout button CSS. Recheck `{% stylesheet %}`-styled components and
   `.shopify-payment-button`.
3. A `defer` theme script moved above the tag now runs before the compiled
   `{% javascript %}` bundle. This matters only if it reads something that bundle
   defines.

Import maps and app embeds need nothing: app assets land after
`content_for_header` regardless.

**Keep the layout head cheap** (high). Shopify must render everything above the
tag before it sends part one. A `meta-tags` snippet that loops `collections` or
`product.variants` in the head delays the whole benefit.

**App-supplied layouts** (high). A page-builder app whose layout captures or
rewrites `content_for_header` disables streaming for every page using it. Check
every file in `layout/` you did not write.

**Verify on the live theme only.** In DevTools, select the document request and
open Timing. On a streamed page, "Waiting for server response" ends at part one
and "Content download" covers section render; stylesheet and font requests
should start during Content download. Compare medians across several reloads.
This is a layout edit: confirm with the merchant or theme owner before
shipping it.

## LCP: images

**`image_url` + `image_tag`, never hand-built CDN URLs** (medium, but this is
the most common real finding). Each manual width in a `srcset` is a separate
filter call; `image_tag` builds every width server-side in one operation and
emits `width`/`height`, focal point, format negotiation, and CDN cache params
for free.

```liquid
{{ product.featured_image
   | image_url: width: 800
   | image_tag: widths: '400, 600, 800',
                sizes: '(min-width: 750px) 25vw, 50vw',
                loading: 'lazy' }}
```

**Position-aware loading with `section.index`** (medium). Sections 1-3 are
above the fold on most templates:

```liquid
{% unless section.index > 3 %}
  {{ image | image_tag: loading: 'eager', fetchpriority: 'high' }}
{% else %}
  {{ image | image_tag: loading: 'lazy' }}
{% endunless %}
```

Use `unless section.index > 3`, not `if section.index <= 3`. `section.index` is
nil for static sections and in some editor contexts, and nil comparisons are
falsey in Liquid, so the `unless` form fails safe to eager.
location (see the TTFB section above), so footer sections 1-3 also go eager under
this rule. That costs little; a footer `fetchpriority: 'high'` costs more.

**`<picture>` for art direction only** (medium): genuinely different crops for
mobile and desktop. If it is the same image at different sizes, `srcset` via
`image_tag` is correct and cheaper.

The LCP rules themselves (never lazy-load it, `fetchpriority="high"`, no
animation, no CSS `background-image`, no dialog hijack) are platform-independent
and live in the `performance` skill.

## LCP and FCP: JavaScript and apps

**Render-blocking apps** (high). Web Almanac 2022 measured a median blocking
time of **1.4s** across the ten most popular third parties. Chat widgets, review
apps, email capture, and analytics are the usual culprits. Where the tag sits in
`theme.liquid` it is theme-owned and movable:

```liquid
<script>
  window.addEventListener('load', function () {
    var s = document.createElement('script');
    s.src = 'https://widget.app.com/script.js';
    s.async = true;
    document.head.appendChild(s);
  });
</script>
```

Remove outright: unused apps, duplicate apps, and anything costing 500ms+ of
blocking time. Prefer native alternatives (review metafields, native forms) over
a widget.

**Server-render essential content, then enhance** (high). Product image, title,
price, nav, and collection cards belong in the Liquid response. Update them with
the Section Rendering API rather than re-rendering client-side:

```javascript
const url = `${location.pathname}?variant=${variantId}&sections=${sectionId}`;
const data = await (await fetch(url)).json();
document.getElementById(`shopify-section-${sectionId}`).outerHTML = data[sectionId];
```

`?sections=a,b,c` returns JSON, max 5 sections, 400 past that; a missing section
comes back `null` inside a 200. `?section_id=x` returns raw HTML and 404s if the
section does not exist.

**A/B anti-flicker snippets** (high). Synchronous, render-blocking, and
routinely over 2s of FCP. Gate the whole snippet on a `settings_schema.json`
checkbox and leave it off between experiments.

**Import maps instead of a bundler** (low, but free). One fetch and one cache
entry for a module shared across sections, no build step:

```liquid
<script type="importmap">
{ "imports": { "cart-api": "{{ 'cart-api.js' | asset_url }}" } }
</script>
```

**Fire critical external-API requests before DOM ready** (high, LCP). When a
collection grid, search results, filter panel, or reviews block must come from an
external API (Searchanise, Boost, Algolia, Klevu, review apps), the usual pattern
waits for `DOMContentLoaded` and then fetches. On a streamed page that waits for
the whole section render first. Load the bundle `async` from the head, above
`content_for_header`, gated on `request.page_type`; send the `fetch` as soon as
the script runs, with inputs from a Liquid-written JSON block (not the DOM, not
`Shopify.*`); then await both the response and DOM ready before rendering. Check
`document.readyState` rather than adding a bare `DOMContentLoaded` listener: an
`async` script can arrive after the event fired. Do not use `defer` for this
script, and do not inline it (inline scripts ignore `async`). When the vendor
script is app-owned, this is a vendor ask, not a theme fix.

**Load interaction-only JS on interaction** (high, INP). Dynamic `import()` inside
the event listener, so the module is fetched only when someone opens the
component.

Shopify injects `es-module-shims` itself when the browser needs it. Shipping
your own copy causes duplicate execution. Verify in Safari 16.3 and earlier.

## INP

**Build hidden DOM on first open** (medium). Mega menus, size charts, and cart
drawers rendered at page load cost parse, style, and layout for every visitor.
Use a `<template>` and clone on first interaction, a `data-` attribute for
server-generated markup, or fetch the section on demand. Past ~1,000 nodes,
yield between chunks with `scheduler.yield()`.

**Merge duplicate mobile and desktop menus** (medium). Two navigation trees in
the DOM doubles the cost of every style recalculation on the page. Build one and
restyle it.

**Cancel in-progress view transitions on interaction** (medium): cross-document
transitions block rendering between snapshot and response, and an interaction in
that window has nowhere to paint. Pattern is in the `performance` skill.

**Debounce, throttle, and yield** (medium): platform-independent; see the
`performance` skill and its `references/INP.md`.

## CLS and FCP: CSS and fonts

**Stylesheet count** (high). Shopify's targets: fewer than 50 on a collection
page, 40 on the homepage, 30 on a product page. Over 100 (collection) or 50
(product) is a finding. The cause is almost always a per-card snippet that
`<link>`s its own stylesheet inside a loop:

```liquid
{%- comment -%} snippets/card-product.liquid {%- endcomment -%}
{%- unless skip_styles -%}
  <link rel="stylesheet" href="{{ 'component-card.css' | asset_url }}">
{%- endunless -%}

{%- comment -%} the calling section {%- endcomment -%}
{%- assign skip_card_styles = false -%}
{%- for product in collection.products -%}
  {% render 'card-product', product: product, skip_styles: skip_card_styles %}
  {%- assign skip_card_styles = true -%}
{%- endfor -%}
```

50 cards then load one stylesheet instead of 50.

**Stylesheet subsetting** (medium). Shopify ships only the `{% stylesheet %}`
CSS belonging to files actually in the page's render tree. It breaks silently
when one file defines a class and a different file uses it; if the defining
file is not rendered, the consuming file renders unstyled. Keep each class in
the file that uses it, or move genuinely shared rules to `base.css`.

**Self-host fonts on the Shopify CDN** (medium). Assets in `/assets/` serve from
`cdn.shopify.com` alongside the storefront: no extra DNS lookup, no extra
connection. Google Fonts is the slowest of the three options Shopify documents.

```liquid
<style>
  @font-face {
    font-family: 'Montserrat';
    font-style: normal;
    font-weight: 400;
    font-display: swap;
    src: url('{{ "montserrat-v31-latin-regular.woff2" | asset_url }}') format('woff2');
  }
</style>
{{ 'montserrat-v31-latin-regular.woff2' | asset_url | preload_tag: as: 'font', type: 'font/woff2' }}
```

Use `preload_tag`, not a hand-written `<link rel="preload">`. The rendered markup
is byte-identical, so swapping one for the other in the DOM is not itself an
optimization. The real difference is invisible in the HTML: `preload_tag` also
adds the asset to the response's `Link: <url>; rel=preload` header, which the
browser acts on before it parses any markup. A hand-written tag only gets found
at parse time. That header is also what consumes the preload budget below.

**Fallback font metrics for swap CLS** (high, CLS). `font-display: swap` causes
a reflow when the web font's metrics differ from the fallback: 0.05-0.15 CLS on
text-heavy pages. First set a unitless `line-height` (fixes the vertical half).
Then declare a fallback `@font-face` with `src: local("Arial")` (or the closest
system font) and `size-adjust`, `ascent-override`, `descent-override`,
`line-gap-override` measured per font pair. Fontaine and Capsize generate the
values. Shopify's suggested pairs: Futura/Trebuchet MS, Helvetica Neue/Arial,
Playfair Display/Georgia, Montserrat/Verdana, Lato/Tahoma. A smaller font file
does not reduce this shift. Uploaded fonts are served as-is, so subset them
before upload.

**System fonts remove the font cost entirely** (medium, FCP). Font picker
handles are `system_ui_n4` / `sans_serif_n4` (with `i4`, `n7`, `i7`), `serif`,
`mono`. `system` is not a valid handle. Branch on `font.system?` to emit the
native stack. Offer this when the brand has no required typeface.

**Reserve space for app-injected content** (medium): review stars, email
capture, subscription widgets. Measure the rendered height once and hard-code a
`min-height` in the theme. This is the single most common CLS cause on a
Shopify store and it is theme-fixable even though the widget is not.

## Platform and CDN

**Request proxies in front of Shopify** (high). A Cloudflare, Fastly, or WAF
layer between the browser and Shopify adds a network hop to every request, for
work Shopify's edge already does. Detect it three ways:

```bash
dig +short CNAME www.store.com      # Shopify's own setup resolves to shops.myshopify.com
curl -sI https://store.com | grep -iE 'via|cf-|x-cache|server'
curl -so /dev/null -w '%{time_starttransfer}\n' https://store.com          # compare
curl -so /dev/null -w '%{time_starttransfer}\n' https://store.myshopify.com
```

Median several samples; a single curl is noise. Fix is a DNS change, not a theme
change. If the proxy is load-bearing for compliance, route HTML through it and
serve assets straight from the Shopify CDN.

**Preload budget** (medium). Shopify sends at most **10 preload `Link` headers
per response**, and its own automatic render-blocking preloads fill those slots
first. Add more than one or two of your own and the image preload you actually
wanted gets dropped.

**Do not preconnect to `cdn.shopify.com`**: the platform already sends it. Theme
Check flags this as `CdnPreconnect`. Theme assets also now load from a `/cdn`
path on the store's own domain, so that connection may never be used.

**Preconnect only to late-discovered third-party origins** (medium). Shopify
already sends `Link` preconnects for the first three third-party origins that
serve render-blocking resources in the rendered `<head>`. A hand-written hint for
those origins is redundant and arrives later. Valid targets: origins first hit by
JS at runtime, app embeds, or CSS (for example `fonts.gstatic.com`, a video host,
a payment widget). Limit to one or two; use `dns-prefetch` for the rest; never
both on one origin. Add `crossorigin` only when the first requests are CORS
(fonts, `fetch`). A domain that serves both needs two hints. Do not preconnect to
`monorail-edge.shopifysvc.com` or the store's own domain.

**The preload budget includes `stylesheet_tag: preload: true` and
`image_tag: preload: true`.** Both emit `Link` headers into the same capped slot
pool as `preload_tag`.

**Speculation rules are already on** (medium). Since June 2025 Shopify sends a
`Speculation-Rules` header on every Liquid storefront: `prefetch`, `conservative`
eagerness (mousedown/touchdown). Measured gain: about 220ms median on desktop,
about 20ms on mobile, Chromium only. A theme can add its own
`<script type="speculationrules">`, for example `prerender` with `moderate`
eagerness on `[data-instant-navigation]` links from collection to product.
`moderate` adds about 2-4% storefront requests. `prerender` runs JS, so check
that analytics and pixels do not fire on pages the shopper never opens. Shopify
clears prefetch/prerender caches with `Clear-Site-Data` on cart change; other
state is the theme's problem. Never use `immediate` on a collection grid.

**Fake performance apps.** Shopify documents the pattern explicitly: LCP
hijacking with a transparent overlay, `Chrome-Lighthouse` user-agent branching,
and API patching that defers loads past the measurement window. Detection and
the general shape are in the `performance` skill. On a store, the tell is a
20+ point PSI-vs-DevTools gap alongside flat or worsening CrUX.

## Static checks before you scan

Theme Check catches several of these without a Lighthouse run at all:

```bash
shopify theme check
shopify theme check --path sections/
shopify theme check --auto-correct
```

| Rule | What it catches |
|---|---|
| `AssetSizeCSS` | CSS assets over 100,000 bytes (default) |
| `AssetSizeJavaScript` | JS assets over 10,000 bytes (default) |
| `ParserBlockingScript` | `<script>` without `defer` or `async` |
| `RemoteAsset` | Assets on external domains instead of the Shopify CDN |
| `CdnPreconnect` | Redundant preconnect to the Shopify CDN |
| `ImgWidthAndHeight` | `<img>` missing `width`/`height` (CLS) |
| `PaginationSize` | `paginate` beyond `maxSize: 250` |
| `ContentForHeaderModification` | `content_for_header` filtered, wrapped, or captured (breaks streaming) |
| `AssetPreload` | Hand-written preloads that should use `preload_tag` (same markup, but only the filter emits the `Link` header) |

## Mobile items in the same catalog

Shopify lists two mobile items here that overlap the a11y skill: no full-screen
dialog or interstitial over main content on load (it can also become the LCP
element), and tap targets of at least 48 x 48 px. Route a full audit to
`a11y-audit`; report the interstitial here when it is the LCP element.

Two other Shopify-native tools worth knowing: the **Shopify Lighthouse CI GitHub
Action**, which uploads a theme to a benchmark shop and scores it the way the
Theme Store does, and **Theme Inspector for Chrome**, which profiles Liquid
render time per section, the only direct way to see which section is costing
TTFB.

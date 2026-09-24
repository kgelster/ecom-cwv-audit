# Shopify theme performance rules

Shopify's own theme performance catalog, condensed. Source:
https://shopify.dev/docs/storefronts/themes/best-practices/performance
(read 2026-08-18). Impact labels are Shopify's, not this skill's.

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
Check flags this as `CdnPreconnect`.

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
| `AssetPreload` | Hand-written preloads that should use `preload_tag` (same markup, but only the filter emits the `Link` header) |

Two other Shopify-native tools worth knowing: the **Shopify Lighthouse CI GitHub
Action**, which uploads a theme to a benchmark shop and scores it the way the
Theme Store does, and **Theme Inspector for Chrome**, which profiles Liquid
render time per section, the only direct way to see which section is costing
TTFB.

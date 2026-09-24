# Field data: CrUX and Shopify RUM

Field data answers *which page is costing money*. Lab data answers *why*. Running
stage 1 without stage 0 optimizes whatever page you happened to test.

## CrUX API (primary)

Free. Requires a Google Cloud API key with the **Chrome UX Report API** enabled.
150 queries/minute/project, no paid tier, no billing account needed.

1. https://console.cloud.google.com/apis/credentials -> Create API key
2. Enable "Chrome UX Report API" on the same project
3. `export CRUX_API_KEY=...` (or put it in a gitignored `.env` your shell loads)

```bash
curl -s -X POST \
  "https://chromeuxreport.googleapis.com/v1/records:queryRecord?key=$CRUX_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"formFactor":"PHONE","origin":"https://store.com",
       "metrics":["largest_contentful_paint","interaction_to_next_paint",
                  "cumulative_layout_shift"]}'
```

`origin` aggregates every page; `url` targets one page. Not both in one request.

### Why there is no keyless path

The PageSpeed Insights endpoint (`pagespeedonline/v5/runPagespeed`) returns the
same CrUX field data and works without a key in principle. In practice it returns
**429 immediately** from a normal IP. Verified 2026-08-09. Treat it as
unavailable, not as a fallback.

### LCP subparts in field data

CrUX returns the same four phases the DevTools Performance panel computes, which
means LCP can be attributed to a phase from real users and not only from a lab
trace. `field.py` requests all four:

```
largest_contentful_paint_image_time_to_first_byte
largest_contentful_paint_image_resource_load_delay
largest_contentful_paint_image_resource_load_duration
largest_contentful_paint_image_element_render_delay
```

This is the strongest signal available for prioritization, because it is real
users and it already says which phase to attack.

### 404 means insufficient data

A 404 means CrUX has no sample for that origin or URL, usually low traffic. It is
**Undetermined**, never a pass. A low-traffic page silently reported as passing is
the most dangerous failure mode in this stage.

### Thresholds (p75, 28-day rolling)

| metric | good | poor |
|---|---|---|
| LCP | <= 2500ms | > 4000ms |
| INP | <= 200ms | > 500ms |
| CLS | <= 0.10 | > 0.25 |
| FCP | <= 1800ms | > 3000ms |
| TTFB | <= 800ms | > 1800ms |

## Shopify RUM (better, but gated)

Shopify collects its own RUM from real storefront sessions and segments it **by
page type**, which beats CrUX for a store: CrUX samples per URL and gives nothing
for low-traffic pages, while Shopify knows "all product pages" as a cohort.

It is reachable through `shopifyqlQuery` on the GraphQL Admin API.

### The gate

Verified 2026-08-09 against live stores. `shopifyqlQuery` returns `ACCESS_DENIED`
unless the token has:

- the **`read_reports`** access scope, **and**
- **Level 2 protected customer data** approval

`read_analytics` is **not** sufficient. Of five client tokens checked, none
qualified; the closest had `read_analytics` only.

`field.py --shop <domain> --token-env <VAR>` probes this and reports the exact
gate rather than returning empty.

### GraphQL shape

The docs imply a union with `TableResponse` and an object list of parse errors.
Neither exists. The real shape, introspected from a live store on API 2026-07:

```graphql
query Q($q: String!) {
  shopifyqlQuery(query: $q) {
    parseErrors                       # String scalar, NOT an object list
    tableData {
      columns { name dataType displayName }
      rows                            # JSON scalar
      rowMetadata
    }
  }
}
```

Rows come back as JSON, and `parseErrors` is a plain string. A selection set on
`parseErrors` is a GraphQL error, not a ShopifyQL error - do not mistake one for
the other while debugging.

### Enabling it

Add `read_reports` to the store's custom app, reinstall it, then request Level 2
protected customer data access. Until that is done, this stage runs on CrUX only
and the report should say so.

### Operator path without the API

The Web Performance dashboard is visible in the Shopify admin under Online Store
> Themes, with per-metric "over time" reports and a ShopifyQL query editor. If
the API gate is closed and the number is needed once, read it there rather than
building around the gate.

## What Search Console does not give you

The Core Web Vitals report in Search Console is UI-only. **The Search Console API
has no Core Web Vitals endpoint.** The `seo` CLI's GSC access does not reach CWV
data. Do not plan around it.

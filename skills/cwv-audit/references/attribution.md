# Ownership: theme vs app

Every performance finding on a Shopify storefront has an owner, and the owner
determines whether a fix is even possible in theme code. A blocking script from
Rebuy is not a theme fix: the DOM and the bundle belong to the app, and the route
is the app's own settings, or a ticket to the vendor. A report that ignores this
sends the merchant to edit markup they do not control.

`merge_findings.py` pre-tags every third-party group using `shopify-apps.json`.
Treat the tag as strong but not final: an app widget rendered inside a theme
section carries both markers, and app wins because apps render inside sections.

## Match on hostname, not entity name

Lighthouse's `third-parties-insight` names entities from the `third-party-web`
database, which is unreliable for the Shopify ecosystem. Observed on a live
store: `cdn.ywxi.net` (TrustedSite, a trust-badge app) is reported as
**"Tencent"**. Acting on that name would send someone hunting a CDN that is not
involved.

`shopify-apps.json` therefore matches on request hostname, longest match first,
and falls back to the entity name only for display. Hostnames with no match are
reported as **unidentified**, never guessed.

## Owner values

| owner | meaning | fix route |
|---|---|---|
| `theme` | Liquid, theme CSS/JS, theme assets on the Shopify CDN | Edit the theme. Name the file when you can infer it. |
| `app` | An installed Shopify app's own bundle and DOM | App settings first; vendor ticket when settings cannot reach it. |
| `shopify` | Platform scripts, Web Pixels, Shop Pay | Not removable. Report as fixed overhead, never as an action item. |
| `analytics` | Pixels and tag managers | A business decision as much as a perf one. Report the cost, let the merchant weigh it. |
| `payment` | Klarna/Afterpay/Sezzle/PayPal messaging | Usually Shopify-managed and not merchant-actionable. Low priority. |
| `unknown` | Not in the table | Identify the vendor from the hostname before recommending anything. Then add it to the table. |

## Surface values

- `settings` - the app has a config or custom-CSS field that can reach the
  problem. Try this before escalating.
- `vendor` - only the vendor can change it. Escalate with the measured cost and
  the specific request.
- `theme` - the merchant controls the embed even though the code is third-party.
  Common when a past install injected a script directly into `theme.liquid`;
  ownership is then effectively theme and it can be moved to `defer` or removed.
- `none` - no lever exists. Document and move on.

## The two questions

For any app-owned cost, these decide the recommendation:

1. **Does it need to load on this template?** A wishlist widget on a collection
   grid, a subscription widget on a non-subscription PDP, a loyalty launcher on
   every page. Template scoping is usually available in app settings and is the
   highest-leverage change available without vendor involvement.
2. **Does it need to load before interaction?** Chat launchers, popups, review
   widgets below the fold. Almost all of these can be deferred.

Most app perf problems are a misconfiguration of one of these two, not a defect
in the app.

## Report shape

App-owned findings get their own section, grouped by app, each with the measured
main-thread cost, transfer size, and the route. That section is what the merchant
forwards verbatim to each vendor.

Do not tell a merchant to patch app DOM from theme JavaScript. It breaks on every
app update and it makes the theme responsible for someone else's bug.

## Checkout

Shopify-hosted and locked on non-Plus plans. Report as out of scope and
**Undetermined**, not as passing. Plus stores using checkout extensibility own
their own customizations only.

## Maintaining the table

`shopify-apps.json` is the compounding asset here. Every audit surfaces hostnames that are not yet in it.
Promote the ones you can identify confidently; leave the rest unidentified rather
than guessing.

Entries added from a single live-store audit on 2026-08-09: TrustedSite, Rise.ai,
VerifyPass, DailyKarma, Instant.

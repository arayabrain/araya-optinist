# Analytics Architecture: GTM, GA4 and Consent Mode v2

## Executive Summary

- **Google Tag Manager** is the single tag container for the hosted web frontend. Tags are managed in the GTM console, not in code.
- **GA4 is measured through GTM only.** The frontend contains no GA4 or Firebase Analytics SDK, so there is no second measurement path to double-count.
- **Consent Mode v2** defaults every signal to `denied`. No application event is pushed to `dataLayer` until the visitor answers the consent notice.
- **The whole subsystem is inert unless `REACT_APP_GTM_ID` is set at build time.** With it unset, `gtm.js` is never requested, the consent notice never renders, and `trackEvent` is a no-op.
- **Standalone (local) installs are excluded.** Local builds must leave `REACT_APP_GTM_ID` unset, and the route tracker, event middleware and consent notice all suppress themselves when the backend reports standalone mode.

---

## Key Architectural Principles

1. **The SPA owns 100% of pageviews.** The GA4 configuration tag must have its automatic `page_view` turned OFF. `route_change` is the only pageview signal. Leaving the automatic pageview on double-counts every navigation.
2. **No personal data leaves the browser.** `page_path` and `page_location` are always sanitized: query strings are dropped and numeric path segments are collapsed to `:id`. The GA4 tag must be configured to use these values rather than its defaults.
3. **Consent is fail-closed on the signal side.** The consent update is issued through the `gtag` shim defined in `frontend/public/index.html`. If that shim is missing, the signal stays `denied` rather than being assumed granted. Note this governs the Consent Mode signal, not whether `dataLayer` receives application events - those are gated separately on the stored decision.
4. **Custom events are emitted from one place.** A Redux middleware maps fulfilled thunk action types to event names, so no feature component contains event-tracking code and every present or future dispatcher is covered. Pageviews and the public-repository view are the two exceptions: they are not Redux actions, so they come from their own components.
5. **The container ID is a build-time value.** It is baked into `index.html` by CRA's HTML interpolation, so it cannot be changed at runtime and cannot be varied per request.

---

## Architecture Overview

```
public/index.html  (inline, <head>, before the bundle)
  dataLayer = []  ->  gtag shim  ->  consent default: all denied, wait_for_update 500
  guarded loader:  /^GTM-[A-Z0-9]+$/  ->  gtm.js   (skipped when the ID is absent)
        |
        v
index.tsx
  initAnalyticsConsent()   re-applies a stored decision before the first render
  <ConsentBanner/>         sibling of <App/>, wrapped in ErrorBoundary
        |
        v
utils/analytics.ts   the only writer to dataLayer
  no decision yet  ->  buffer (FIFO, max 10)
  granted          ->  push { ...params, event }
  denied           ->  drop
        ^                        ^
        |                        |
components/common/          store/analyticsMiddleware.ts
RouteChangeTracker          sign_up / login / run_pipeline
route_change                pages/PublicDataview -> view_public_data
```

| Component                                  | Responsibility                                                                    |
| ------------------------------------------ | --------------------------------------------------------------------------------- |
| `public/index.html`                        | Consent Mode defaults, `gtag` shim, guarded container load                        |
| `utils/analytics.ts`                       | Guard, consent state, buffering, path sanitization, the single `dataLayer` writer |
| `components/common/ConsentBanner.tsx`      | Collects and persists the first decision; `CookieSettingsLink` reopens it         |
| `pages/Account/index.tsx`                  | Analytics Cookies switch: withdraws or re-grants an existing decision             |
| `components/common/RouteChangeTracker.tsx` | Pageviews on route transitions                                                    |
| `store/analyticsMiddleware.ts`             | Maps fulfilled thunks to custom events                                            |
| `infrastructure/scripts/ecr_build_push.sh` | Supplies the per-environment container ID at build time                           |

---

## Implementation Details

The head snippet runs before the deferred CRA bundle, so `window.dataLayer` and the `gtag` shim always exist by the time application code runs, and the Consent Mode default is always registered before `gtm.js` can load. The loader is a no-op unless the compiled-in container ID matches the guard, which is what makes an un-configured build byte-for-byte inert at runtime.

Inside the bundle, `initAnalyticsConsent()` runs at module scope in `index.tsx`, before `root.render`, so a returning visitor's decision is applied before the first event can be produced. `trackEvent` is the single writer to `dataLayer`; it holds events in a FIFO buffer (max 10) while no decision exists, flushes them in order on a grant, and discards them on a denial. That buffer is why a first-time visitor's entry pageview is still recorded if they accept, and why nothing at all is recorded if they decline.

Withdrawal needs no reload. `trackEvent` calls `getAnalyticsConsent()` on every event and that reads the in-memory session value before `localStorage`, so flipping the `/account` switch off stops the very next event. The switch appears only once a decision exists. The Cookie settings link reopens the notice without clearing the stored decision, so the earlier choice stays in force until the visitor answers, and Decline then withdraws through the same `setAnalyticsConsent`. Both the notice and the `/account` page subscribe to consent changes, so a reopened notice closes as soon as the switch changes, and neither ever describes a different state from the other.

`RouteChangeTracker` deduplicates on the raw `location.pathname` but pushes the normalized value, so navigating between two different workspace ids is two pageviews rather than one. `analyticsMiddleware` calls `next(action)` first and never alters the action or the dispatch result; it looks up event names by exact action type, so sibling thunks that merely share a type prefix are not captured.

---

## Key Functions Reference

| Function                        | Purpose                                                                                                       |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `isGtmEnabled()`                | Whether a well-formed container ID was compiled in. Every other entry point returns early when false          |
| `getAnalyticsConsent()`         | Current decision: in-memory session value first, then localStorage; unrecognised values read as "no decision" |
| `setAnalyticsConsent(decision)` | Persists, emits the Consent Mode update, and flushes or discards the buffer                                   |
| `initAnalyticsConsent()`        | Re-applies a stored decision at bundle start, before the first event                                          |
| `trackEvent(event, params?)`    | The only `dataLayer` writer. Buffers while undecided, drops when denied                                       |
| `normalizePath(pathname)`       | Collapses numeric segments to `:id` to bound GA4 cardinality and keep internal ids out                        |

---

## Monitoring and Metrics

No CloudWatch metrics or alarms: this subsystem is entirely client-side and emits no backend telemetry. Observability is the GA4 property itself, plus GTM Preview and GA4 DebugView for verification.

---

## Configuration

| Source (first match wins)                    | Where it is set                           |
| -------------------------------------------- | ----------------------------------------- |
| `REACT_APP_GTM_ID_<ENVIRONMENT>`             | `infrastructure/.env.deploy` (gitignored) |
| String parameter `/<environment>/optinist/gtm-id` | SSM Parameter Store, `ap-northeast-1`     |

When neither is set, GTM, GA4, the consent notice and all event tracking are disabled.

`ecr_build_push.sh` keys both on the Terraform `environment` output. Production (`subscr`) reads `REACT_APP_GTM_ID_SUBSCR` or `/subscr/optinist/gtm-id`; development reads `REACT_APP_GTM_ID_DEVELOPMENT` or `/development/optinist/gtm-id`. Per-environment keys prevent a development build from being published with the production container, which would pollute the property permanently. A missing parameter means analytics off; any other SSM error fails the build, so an access problem cannot silently ship a build without analytics. The build log states which source supplied the ID. `.env.deploy` is sourced in a subshell, so unrelated assignments in it cannot affect the build.

There is deliberately no placeholder parameter for production: absence is the off switch. See [DEPLOYMENT_PROCEDURE.md](DEPLOYMENT_PROCEDURE.md) for the command that creates it.

Optional local override:

```
# infrastructure/.env.deploy
REACT_APP_GTM_ID_SUBSCR=GTM-XXXXXXX
REACT_APP_GTM_ID_DEVELOPMENT=GTM-YYYYYYY
```

The script rewrites `frontend/.env.production` on every build, so editing that file by hand does not survive a deploy.

Format: `GTM-` followed by uppercase letters and digits, enforced identically in three places - the deploy script (hard-fails the build on a malformed value), `isGtmEnabled()` in `frontend/src/utils/analytics.ts`, and the loader guard in `frontend/public/index.html`. The un-interpolated literal `%REACT_APP_GTM_ID%` that CRA leaves behind when the variable is absent fails all three.

---

## Events

| Event              | Emitted by            | Trigger                                                                                                    |
| ------------------ | --------------------- | ---------------------------------------------------------------------------------------------------------- |
| `route_change`     | `RouteChangeTracker`  | Initial location and every subsequent distinct pathname. Carries sanitized `page_path` and `page_location` |
| `sign_up`          | `analyticsMiddleware` | `registerUser.fulfilled`                                                                                   |
| `login`            | `analyticsMiddleware` | `login.fulfilled`, excluding admin impersonation                                                           |
| `run_pipeline`     | `analyticsMiddleware` | `run.fulfilled` or `runByCurrentUid.fulfilled`                                                             |
| `view_public_data` | `PublicDataview`      | Mount of the `/public` page                                                                                |

No event carries parameters other than `route_change`'s sanitized path fields.

---

## Before setting a container ID

Setting `REACT_APP_GTM_ID` is what activates every risk in this document. Until it is set there is nothing to review: no third-party request, no notice, no `dataLayer` writes. The items below are blocking, and none of them can be satisfied from this repository alone.

### Access and supply chain

1. **Restrict GTM container publish rights to a named, minimal set of people, with 2FA enforced on those Google accounts.** GTM is by design a mechanism for injecting arbitrary JavaScript into the page from a console outside this repository. That page runs authenticated scientific workflows and admin impersonation (`proxyLogin`), so publish rights on the container are equivalent to code execution in an authenticated session. This is the single largest security consideration of the whole feature.
2. **Prefer a locked-down container.** Avoid Custom HTML tags; if the threat model warrants it, use server-side GTM.
3. **Enforce the CSP before or soon after enabling the container.** The backend sends `Content-Security-Policy-Report-Only` on the shell (`content_security_policy` in `spa_routing_middleware.py`): a per-response nonce on every inline `<script>`, `www.googletagmanager.com` in `script-src`, the GA4 hosts in `img-src` and `connect-src`, and `'unsafe-inline'` kept in `style-src` for MUI/emotion and react-flow. Violations are logged with a `[CSP]` prefix by `POST /log-report/csp`. Switch the header to `Content-Security-Policy` once those logs are clean on real traffic, with the container enabled on development so its requests are in the reports. Keep Custom HTML tags out of the container: they would need the nonce.

### Compliance

4. **Keep the privacy policy current.** The notice links `/privacy` in a new tab. Section 5.2 uses the wording legal approved on 2026-07-23 with only the tense changed from planned to active and "IP-address anonymization" removed, as GA4 has no such setting; any further change goes back through legal review. The policy describes analytics as active, which holds only in a build with a production container ID (`/subscr/optinist/gtm-id` or `REACT_APP_GTM_ID_SUBSCR`): ship it in the same release that sets the production container ID.
5. **Have the notice copy reviewed.** The policy text was approved by legal on 2026-07-23; the consent notice copy was not part of that review. Put these points to the next review: APPI Article 28(2) asks for the destination country's protection system and the recipient's measures to be given when consent is sought, while privacy policy Section 4.2 offers them on request; the notice says Google processes data in the United States, while the Section 4.2 table says United States and other countries; and `gtm.js` and the Google Fonts stylesheets in `public/index.html` send the visitor's IP address and browser information to Google on every visit whatever the consent choice; and the policy describes consent as opt-in for EEA/UK visitors with opt-out possible elsewhere, while the Service asks every visitor to opt in.
6. **Confirm the consent controls still meet "withdrawal as easy as granting".** Decline and Accept carry identical styling in the notice, and `/account` offers an Analytics Cookies switch that both withdraws and re-grants, taking effect immediately without a reload in that tab. Any visitor, signed in or not, can reopen the notice from the Cookie settings link in the landing page and `/public` footers; `/login`, `/register`, `/terms` and `/privacy` reach `/public` through the header logo. Other open tabs keep their earlier choice until reloaded (nothing listens for the `analyticsConsent` storage key). A restyle of any of these surfaces can regress this.
7. **Keep the GDPR Article 27 position current.** Analytics records how EEA/UK visitors use the Service, which is likely "monitoring" under Article 3(2)(b), and continuous analytics is hard to call "occasional" under the Article 27(2)(a) exemption. Legal approved the current Section 4.2 sentence (no representative, as the Service is not directed at and does not monitor EEA/UK individuals) on 2026-07-23, alongside a Section 5.2 that already described Google Analytics. Revisit it if the Service starts targeting EEA/UK users.

### Verification, once a container exists

The client-side test suite stops at `window.dataLayer`. A correct `dataLayer` does **not** prove correct GA4 data: every automated test still passes if the GA4 tag keeps its automatic `page_view`, if the `page_location` override is missing, or if a custom-event trigger was never created. Run these in GTM Preview and GA4 DebugView / Realtime and record the results.

| #   | Case                                          | Expected result                                                                                                                                 |
| --- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | One client-side route change, GTM Preview     | The GA4 configuration tag fires exactly once and sends `page_view`; `page_location` is the sanitized value with no query string                 |
| 2   | Exercise all five events, GA4 DebugView       | `route_change` (as `page_view`), `sign_up`, `login`, `run_pipeline` and `view_public_data` all appear, with correct names and no PII parameters |
| 3   | Two navigations, GA4 Realtime                 | Exactly two pageviews. Three means the automatic `page_view` was left on                                                                        |
| 4   | Decline at the notice, then navigate          | DebugView receives nothing at all                                                                                                               |
| 5   | Visit `/account-manager?email=...`, DebugView | `page_location` carries no email and no query string                                                                                            |
| 6   | Trigger cross-check in the container          | All four custom-event triggers exist and fire                                                                                                   |
| 7   | Privacy link from `/account-manager?email=`   | Accept in the new tab: `page_referrer` carries no email and no query string                                                                     |
| 8   | Accept, then switch off on `/account`         | Scroll, click an outbound link and navigate: the browser's network panel shows no further `/g/collect` request                                  |
| 9   | Accept, then Cookie settings on `/public`     | Decline and navigate: the browser's network panel shows no further `/g/collect` request                                                         |

---

## Required GTM container configuration

The code side is inert without these console-side settings. Configure them **before** setting `REACT_APP_GTM_ID`, or measurement will be wrong from the first hit.

1. **GA4 configuration tag: automatic `page_view` OFF.** Add a Custom Event trigger on `route_change` and send `page_view` from it. Leaving the built-in pageview on produces two pageviews per navigation.
2. **Override `page_location` and `page_title` on the GA4 tag** using the `page_location` and `page_path` dataLayer variables. GA4 otherwise defaults `page_location` to `document.location.href`, and live routes carry personal data in their query strings: `/account-manager?email=...&name=...`, `/dataview` and `/public` (`?user_name=...&uid=...&name=...&workspace_name=...`), and `/subscription/thanks?session_id=...` and `/subscription/failed?session_id=...`. The frontend cannot prevent that from the tag's own default, and query strings must not reach Google, so this override blocks setting the production container ID.
   Override `page_referrer` the same way, with `document.referrer` stripped of its query string. GA4 defaults it to `document.referrer`, which keeps the full same-origin URL: the notice's Privacy Policy link opens a new tab with `rel="noopener"` only, so accepting there from `/account-manager?email=...` would send that URL as the referrer. This is a rollout gate too.
3. **Enable GA4 "Redact data"** (email and query-parameter redaction) as defence in depth, with the query parameters `email`, `name`, `session_id`, `user_name`, `uid` and `workspace_name`. It does not cover IP addresses.
4. **Create Custom Event triggers** for `sign_up`, `login`, `run_pipeline` and `view_public_data`.
5. **Do not put tags on the Initialization or All Pages triggers.** See the returning-visitor row under Edge Case Handling.
6. **`sign_up` fires when registration is submitted, not when the email is verified.** Verification is a separate later step, so this metric counts attempted registrations.
7. **Turn enhanced measurement off on the GA4 web data stream**, including page changes based on browser history events. Its scroll, outbound-click, site-search, form, video and file-download events and its history-change pageviews come from the Google tag's own listeners, not from `trackEvent`: they would collect more than the actions listed in this document, add a second pageview per client-side navigation, and withdrawal on `/account` stops `trackEvent`, not those listeners. This blocks setting the production container ID.

---

## Edge Case Handling

| Case                                                                        | Behaviour                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Visitor has not answered the notice                                         | Events are buffered in memory (up to 10, in order) and pushed only if the visitor accepts. Declining discards them and every later event is a no-op.                                                                                                                                                                                                                                                                                                                                                                     |
| Visitor declines                                                            | No `route_change`, `sign_up`, `login`, `run_pipeline` or `view_public_data` event is ever pushed. The container is still loaded and `analytics_storage` stays `denied`, so whether a tag on an Initialization or All Pages trigger emits a cookieless ping is decided entirely by the container configuration, not by this code.                                                                                                                                                                                         |
| Returning visitor who accepted                                              | The stored decision is re-applied at bundle start, before any event is pushed. `wait_for_update: 500` only helps a warm cache: the main chunk is ~2 MB gzipped, so on a cold load the wait expires first. Any tag on an Initialization or All Pages trigger will therefore evaluate against `denied` for a returning visitor and produce a cookieless, unattributed hit. Keep such tags out of the container; if one is ever needed, the stored grant must be replayed inline in `index.html` before the loader instead. |
| `localStorage` refuses writes (Safari "block all cookies", private modes)   | The decision is held in memory for the session, so tracking works as chosen; the notice reappears on the next reload.                                                                                                                                                                                                                                                                                                                                                                                                    |
| Corrupted stored value                                                      | Treated as "no decision"; the notice is shown again.                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Backend is unreachable                                                      | The router never mounts, so no pageview is recorded for the error screen, and the consent notice is not shown over it. Deliberate.                                                                                                                                                                                                                                                                                                                                                                                       |
| Client-side redirect on load, e.g. unauthenticated `/dashboard` to `/login` | Both paths are recorded. Known inflation, inherent to client-side routing.                                                                                                                                                                                                                                                                                                                                                                                                                                               |

---

## Known gaps

- **Consent does not sync across tabs.** Granting in one tab leaves another tab's notice up until it reloads.
- **Per-route head metadata only covers the sitemap routes.** The backend rewrites the canonical, URL, title and description head tags (including the `og:` and `twitter:` variants) for `/login`, `/register` and `/public` when it serves the shell (`rewrite_route_metadata` in `spa_routing_middleware.py`), and `/robots.txt`, `/sitemap.xml` and `/llms.txt` are served as real files instead of the shell. Other public routes (`/terms`, `/privacy`, ...) still carry the root page's metadata; add them to the sitemap, `ROUTE_HEAD_METADATA` and `frontend/src/const/routeHeadMetadata.json` (which `RouteHeadMetadata` applies after in-app navigation) together. `og:image` is also a relative URL, which the OG spec requires to be absolute, so some scrapers drop the preview image.
- **Search Console ownership is not verified.** DNS TXT or a `google-site-verification` meta tag are the robust methods; the GTM method additionally requires publish rights on the container.
- **e2e consent seeding is only partial.** `frontend/e2e/global-setup.ts` seeds `analyticsConsent=denied` into the authenticated storage state, but specs that build their own unauthenticated context (the login and registration flows) will still meet the notice once a container ID is set for the tested build.

---

## Testing

| Suite                                                                  | Covers                                                                                                                                                                                                                                        |
| ---------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frontend/src/utils/__tests__/analytics.test.ts`                       | Guard, consent storage, buffering and flush, path sanitization. Also reads the inline scripts out of `public/index.html` and evaluates them, so the `gtag` shim, the consent defaults and the loader guard are asserted rather than described |
| `frontend/src/components/common/__tests__/ConsentBanner.test.tsx`      | Visibility rules, both decisions, equal button prominence, accessibility role, reopening from Cookie settings and withdrawal after it                                                                                                         |
| `frontend/src/pages/Account/__tests__/AnalyticsConsent.test.tsx`       | The withdrawal switch: visibility rules, withdrawal and re-grant, and that withdrawal silences the next event without a reload                                                                                                                |
| `frontend/src/components/common/__tests__/RouteChangeTracker.test.tsx` | Initial and subsequent pageviews, dedupe, query-string exclusion, standalone suppression                                                                                                                                                      |
| `frontend/src/store/__tests__/analyticsMiddleware.test.ts`             | Action-to-event mapping, impersonation exclusion, standalone suppression                                                                                                                                                                      |

Verify end to end with GTM Preview and GA4 DebugView after setting the container ID: confirm exactly one pageview per navigation and that no query string appears in `page_location`.

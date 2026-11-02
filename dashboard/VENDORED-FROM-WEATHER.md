# Vendored from weather

`dashboard/` starts as a copy of the weather.vallery.net web package: jvallery/weather at commit
`249fba34749ab8f6dd31ab8f1382450d51050fcb` (249fba3), directory `web/`, copied on 2026-11-02 for VD-01
(VerdifyConsultancy/verdify-platform#1121). The rule is copy now, share later (design system §8): no shared
package until a third app wants this look. Copied files keep their paths under `dashboard/` and their
`SPDX-License-Identifier: MIT` header. The issues that copy more weather files add their rows here: `common.tsx`,
`events.tsx` and `current-conditions.tsx` (#1123), `site-header.tsx` (#1124), `history-plot.tsx` and
`linked-plots.tsx` (#1126).

## Copied files

Paths are relative to `dashboard/`; each came from the same path under weather's `web/`.

| Path | Changes |
| --- | --- |
| `.npmrc`, `.node-version`, `tsconfig.json`, `vite.config.ts`, `vitest.config.ts`, `react-router.config.ts` (`ssr: true`), `eslint.config.js`, `app/entry.client.tsx` | None; byte-identical. `tsconfig.json` still includes `e2e/**/*.ts`, which matches nothing until #1128 adds `e2e/`. |
| `public/fonts/ibm-plex-sans-latin-wght-normal.woff2`, `public/fonts/ibm-plex-mono-latin-400-normal.woff2`, `public/fonts/OFL-sans.txt`, `public/fonts/OFL-mono.txt` | None; byte-identical (hashes under Fonts). |
| `package.json` | Renamed `verdify-dashboard-web`. Removed `prom-client`, `@playwright/test`, `@axe-core/playwright` and the `test:browser` script (#1128 adds Playwright and axe back). Added `supertest` 7.3.1 and `@types/supertest` 7.2.1 for `app/server.test.ts`. Every other pin is weather's. |
| `server.mjs` | Exports `createApp()` (no listener; tests pass `env`, `sourceRevisionPath`, a stand-in `render` handler and a `log` sink), `createServer()` (weather's request, header and keep-alive timeouts and `maxHeadersCount` 64) and `start()`, which runs only when `server.mjs` is the entry module. Removed `prom-client`, the second Express app and `METRICS_PORT`: one listener on `PORT` (3000). The Host pin reads `PUBLIC_ORIGIN` (default `https://dashboard.verdify.ai`; anything but an https origin stops the start, and the error names the variable, not the value) instead of `PUBLIC_HOST`, and the `localhost` exception keys on `DASHBOARD_CI_IN_CLUSTER=1` instead of `WEATHER_CI_IN_CLUSTER`. `GET /healthz` answers `{"status":"ok"}` and `GET /readyz` `{"status":"ready","source_revision":…}` (OPS-01, OPS-02), from `SOURCE_REVISION` when it is 40 hex, else `/etc/verdify/source-revision`, else `unknown`, read once at start. Log lines name `service: "verdify-dashboard-web"`; route templates are `/`, `/healthz` and `/readyz`, anything else `/other`. Weather's SSR failure metric becomes a bounded `failure` field on the request's line (`overloaded`, `deadline`, `render_error`), and a response that closes unfinished is logged once with `aborted: true` and gives its render slot back (`app/server.test.ts`). The limiter keeps 4 slots, the 15 s deadline and `Retry-After: 5`, with the text "Dashboard is busy. Please retry shortly."; only `/__manifest` skips it (weather also exempted its loader-less pages, and every dashboard page is loader-less: data loads in the browser). The render-failure text is "Dashboard is temporarily unavailable." The static directory resolves next to `server.mjs` instead of the working directory, and the nonce request header is `x-dashboard-csp-nonce`. |
| `security-headers.mjs` | Removed `EMBED_PARENT`, `isEmbedPath` and the embed `frame-ancestors` exception: every response carries `frame-ancestors 'none'` and `X-Frame-Options: DENY`. The signature is `securityHeaders(nonce)`, since no path changes the policy. |
| `app/root.tsx` | Removed the embed branch and `SiteHeader` (#1124 copies `site-header.tsx` and builds the shell). The footer keeps only "Verdify dashboard" and the two IBM Plex licence links. The error page reads "Dashboard is busy", "Page not found" or "The dashboard is temporarily unavailable", with "Return to Now"; weather's 400 dates-and-filters case is gone. Nonce header renamed as in `server.mjs`. |
| `app/routes.ts` | One route: `index("routes/now.tsx")`, the Now page head. None of weather's routes are copied. |
| `app/entry.server.tsx` | Removed `prom-client`. `handleError` is a no-op that replaces React Router's default, which writes each error and its stack to stderr (a 404's names the request path); the request's log line records the status. Nonce header renamed. |
| `app/lib/chunk-reload.ts`, `app/lib/chunk-reload.test.ts` | Storage key `verdify-dashboard:chunk-reload`; test URLs and messages renamed. `entry.client.tsx` imports it, and it is the only recovery for a tab held across a release, because this image does not retain the previous build's assets as weather's does. |
| `app/styles.css` | Line 2's comment replaced. Copied byte for byte from weather's `web/app/styles.css`: lines 1 and 3-52 (font faces, the `:root` tokens at lines 6-15, base), 54-83 (Shell), 85-97 (Type helpers), 99-112 (Page structure), 114-150 (Cards, tags, buttons, chips, with filters, tables and kv), 152-178 (Now: hero and stats), 207-219 (Compact event rows), 324-341 (Plots, with summary stats), 412-413 (Error page, which `root.tsx`'s error page uses) and 415 (reduced motion). Left out: media cards (camera), timelapses calendar, day, recording, keograms, stills, almanac, search, editorial and editor, embed card, and the trailing editor poster rules. Copied sections are whole, so a few unused rules stay (the `.units` toggle in Shell, `.search-form` in Page structure, `.pager` and `.unit-form`). The design system §2.2 block is appended verbatim as the file's last block. `app/styles.test.ts` checks the tokens, the order and the exclusions. |
| `app/lib/format.ts` | Kept on America/Denver: `localTime`, `plotTime` (`longOffset`, `hourCycle: "h23"`), `localDate`, `ageSeconds`, `dateShift`, `dayTitle`, `monthTitle`, `duration`, `compass`, `localDayStart` and `TIME_UNAVAILABLE`. `shortTime` and `shortDateTime` are 24-hour ("06:42", "Oct 27, 06:42") instead of weather's 12-hour, and a time in the repeated fall-back hour adds its offset (`repeatedLocalTime`, new). `duration` puts a no-break space before its unit. Added `TIME_ZONE` and `formatValue(value, unit)` with the fixed units of design system §5.4, and `formatNumber(value)` (#1123), a unitless value at up to 3 decimals for a clamp's applied value. Removed `CAMERA_LABELS`, the °C and metric conversion (`conversionAvailable`, `metricValue`), `ageLabel` and `metricLabel` (typed on weather's API metrics) and `humanize` with weather's reason words (the dashboard's words table is #1124's). |
| `app/lib/polling.ts` | `nextPollDelay(failures, baseMs = 15_000)` doubles per failure up to 120 s for any base (weather capped three doublings of 15 s); a base above 120 s never shortens. Removed `CURRENT_QUERY_KEY`; added `MAX_POLL_DELAY`. |
| `app/lib/use-current.ts` | `useCurrent(initial, apiUnavailable, options)` became `usePolledQuery(queryKey, fetcher, intervalMs)`. There is no SSR data to seed, so it fetches on mount. Weather's visibility pause and its refresh on resume are kept, the resume refresh now always on; the jitter option is gone. Stale time is the interval, the 6 s request timeout stays, and failures back off through `nextPollDelay`. The hook aborts its own requests instead of reading TanStack's signal, which would cancel the request whenever the last observer unsubscribes and make a StrictMode remount fetch twice. |
| `app/lib/polling.test.tsx` | Weather's cadence, deduplication, hidden-tab cancel, backoff and resume cases, rewritten for `usePolledQuery` with a 60 s case and `nextPollDelay` bounds. The cases that test `CurrentConditions` (live region, authoritative unavailable data, loader failure, a cancelled poll after invalidation, a device clock running ahead) stay with that component for #1123; the jitter case went with the option. |
| `app/security-headers.test.ts` | Embed cases removed; it checks the no-framing policy, the nonce and the other headers. |
| `app/root.test.tsx` | The busy, unavailable and `Retry-After` cases with the dashboard's wording, plus a 404 case and a check that Now renders only its page head and makes no request. Embed cases removed. |
| `app/components/common.tsx` (#1123) | Kept `PageHead` unchanged. Removed `Status` (`badge.tsx` draws states now), `SEGMENT_LABELS`, `videoPath`, `VideoLink`, `SourceInterval`, `Pager`, `cycleLabel` and the `unit-link` import (timelapse pages and the units toggle). Added `LocalTime`, a 24-hour America/Denver `<time>` with the full local form in `title`, and `cls`. |

#1123 builds these from weather files as bases, under new names (paths relative to `dashboard/`):

| Path | Base and changes |
| --- | --- |
| `app/components/event-row.tsx`, `app/components/alert-row.tsx` | From `app/components/events.tsx`. `EventItem`'s `.event` row became `EventRow`: the mono time column (`EventTime`'s `<time>` with the full local form in `title`), then the bold title with its badges, the muted detail and the reason in one `.event-body` column. `ProvenanceBadge` became the `Actor` attribution ("Jason · dashboard", "iris" with AI). `AlertRow` is the same row with a severity rule and word. Removed the camera, video-link, seek and bookmark parts, `eventDetail` and `EventStrip` with its fetch-state words (`Panel` and `alertListEmptyText` say those now). |
| `app/components/hero.tsx`, `app/components/stat-tile.tsx`, `app/components/freshness.tsx` | From `app/components/current-conditions.tsx`: the `.hero` band, `.hero-temp`, the `.hero-meta` freshness line with its `.dot`, the `.stats` tiles (`.stat`, `dt`, `dd`, `small`) and the "All readings" disclosure. Removed the polling (`useCurrent`), unit conversion, dew point, the lightning line and the metric registry: the components fetch nothing, and screens pass API values in. The live-region note is gone, because ages tick silently (design system §7). The server-clock rule (ages count on the server's clock when the device clock is more than 2 s off) moved to `ServerClockProvider` and `serverClockSkew`, and every age ticks on one shared one-second timer. Of the `CurrentConditions` cases VD-01 left here: the device clock running ahead is in `freshness.test.tsx` and `hero.test.tsx`; the authoritative unavailable data is the API-down and null-value cases of `hero.test.tsx`; the live region, the loader failure and the cancelled poll after invalidation have no counterpart, because the hero neither announces ages nor fetches (polling stays `polling.test.tsx`'s). |
| `app/styles.css`, "Dashboard components" section | Not from weather: the rules `app/components/` adds beside weather's classes (the alert rules, banners, toasts, skeletons, freshness dots, the CLEAN and FERT dots, toggle chips, the Stop hover and the phone tiles). It sits between the reduced-motion rule and the §2.2 block, which stays last. Colours are tokens only, white the one literal, as `app/components/badge-contrast.test.ts` checks. |

## Not copied

- `Dockerfile.web` is the model for `dashboard/Dockerfile`, which is new: it builds on the upstream image weather's
  node base imports (`docker.io/library/node:24.22.1-bookworm-slim` pinned by digest, as weather's
  `images/upstream-imports/node/Dockerfile` names it) instead of `registry.vallery.net/jvallery/weather-node-base`,
  has no previous-image asset retention (`retain-web-assets.mjs`), no metrics port and no `LICENSE` copy, and adds
  the `source-metadata` stage of verdify-platform's `api/Dockerfile`.
- `pnpm-lock.yaml` is not copied: `pnpm install` (pnpm 12.15.0 on Node 24.22.1) produced it, starting from weather's
  lockfile so that packages both apps use resolve to weather's versions. It is never edited by hand.
- Left in weather: every route but the index, the components (later issues copy theirs), the fixtures and generated
  clients (#1122 brings the dashboard's), `contract-tools/`, `e2e/`, `playwright.config.ts`, and the remaining `lib/`
  modules. Zod's `z.config({ jitless: true })` travels with each schema module, as in weather; no schema exists yet.

## Fonts

IBM Plex Sans (variable, 100-700) and IBM Plex Mono 400, self-hosted from `public/fonts/` and served at `/fonts/`
with their licence texts beside them. Upstream: `@fontsource-variable/ibm-plex-sans` 5.3.0 and
`@fontsource/ibm-plex-mono` 5.3.0 (weather `docs/research/font-provenance.json`).

| File | SHA-256 |
| --- | --- |
| `ibm-plex-sans-latin-wght-normal.woff2` | `e2291e842cf5af167122a22881a740c7f2dda7716f1e8cd76680264f4a859470` |
| `OFL-sans.txt` | `d0283623ef57e722fd0eb688a8041589670c608ab780cd3612d06ba6f153d3fd` |
| `ibm-plex-mono-latin-400-normal.woff2` | `08949f728dc52d528e69b1667d15c89a5686a4ee9a296ff90983985f99c380f7` |
| `OFL-mono.txt` | `23b0a9d0c6d3f140a0b77e483c5cfa6bba574325ef5cb189ed9f2fec4884533f` |

IBM Plex Sans notice:

> Copyright 2019 IBM Corp. All rights reserved. IBMPlexSans-Italic[wdth,wght].ttf: Copyright 2019 IBM Corp. All
> rights reserved.
>
> This Font Software is licensed under the SIL Open Font License, Version 1.1. The full licence is
> `public/fonts/OFL-sans.txt`.

IBM Plex Mono notice:

> Copyright 2017 IBM Corp. All rights reserved. IBMPlexMono-ThinItalic.ttf: Copyright 2017 IBM Corp. All rights
> reserved. IBMPlexMono-ExtraLight.ttf: Copyright 2017 IBM Corp. All rights reserved.
> IBMPlexMono-ExtraLightItalic.ttf: Copyright 2017 IBM Corp. All rights reserved. IBMPlexMono-Light.ttf: Copyright
> 2017 IBM Corp. All rights reserved. IBMPlexMono-LightItalic.ttf: Copyright 2017 IBM Corp. All rights reserved.
> IBMPlexMono-Regular.ttf: Copyright 2017 IBM Corp. All rights reserved. IBMPlexMono-Italic.ttf: Copyright 2017 IBM
> Corp. All rights reserved. IBMPlexMono-Medium.ttf: Copyright 2017 IBM Corp. All rights reserved.
> IBMPlexMono-MediumItalic.ttf: Copyright 2017 IBM Corp. All rights reserved. IBMPlexMono-SemiBold.ttf: Copyright
> 2017 IBM Corp. All rights reserved. IBMPlexMono-SemiBoldItalic.ttf: Copyright 2017 IBM Corp. All rights reserved.
> IBMPlexMono-Bold.ttf: Copyright 2017 IBM Corp. All rights reserved. IBMPlexMono-BoldItalic.ttf: Copyright 2017 IBM
> Corp. All rights reserved.
>
> This Font Software is licensed under the SIL Open Font License, Version 1.1. The full licence is
> `public/fonts/OFL-mono.txt`.

## Weather's licence

```text
MIT License

Copyright (c) 2026 Jason Vallery

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

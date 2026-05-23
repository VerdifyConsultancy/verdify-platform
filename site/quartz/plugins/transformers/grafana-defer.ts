import { QuartzTransformerPlugin } from "../types"
import { visit } from "unist-util-visit"
import { Root } from "hast"

// Build-time replacement of Grafana embed iframes with progressive-
// enhancement placeholder <div class="grafana-embed">. The companion
// GrafanaEmbeds component (afterDOMLoaded script) upgrades each
// placeholder to either a live iframe (desktop, Android) or a
// /render-served PNG (iOS Safari/Chrome) depending on the user
// agent.
//
// Why placeholders, not iframes: iOS Safari OOM-crashes the tab when
// many Grafana React contexts allocate at once. Static PNGs from the
// grafana-image-renderer service have no JS, no WebSocket, and use
// well under a megabyte each — so iPhone Safari handles them fine.
//
// We carry both URLs forward so the client can pick at runtime:
//   data-iframe-src — original /d-solo/... URL (live, interactive)
//   data-image-src  — /render/d-solo/... URL (static PNG)
// The image-src is computed from the iframe-src by injecting `/render`
// before `/d-solo` and appending `width`/`height` query params sized
// to the panel. Other query params (orgId, panelId, theme, from, to,
// var-*) are preserved verbatim — no URL rewriting beyond the path
// prefix and width/height additions.

interface Options {
  /** URL prefix that identifies a Grafana embed for deferral. */
  matchPrefix: string
  /** Path segment to splice into the URL for image rendering. */
  renderPathSegment: string
  /** Multiplier applied to the iframe pixel height to drive PNG width.
   * The renderer scales the panel; aspect ≈ 16:5 (1000x500 default)
   * for time-series. Width = max(640, height * widthMultiplier). */
  widthMultiplier: number
  /** Auto-refresh interval for the PNG fallback, in ms. */
  refreshMs: number
}

const defaultOptions: Options = {
  matchPrefix: "https://graphs.verdify.ai/",
  renderPathSegment: "/render",
  widthMultiplier: 4,
  refreshMs: 60_000,
}

function toRenderUrl(src: string, panelHeight: number, widthMultiplier: number): string {
  // Splice "/render" before "/d-solo" or "/d" segment.
  let rendered = src
  if (src.includes("/d-solo/")) {
    rendered = src.replace("/d-solo/", "/render/d-solo/")
  } else if (src.includes("/d/")) {
    rendered = src.replace("/d/", "/render/d/")
  } else {
    return src
  }
  const sep = rendered.includes("?") ? "&" : "?"
  const width = Math.max(640, Math.round(panelHeight * widthMultiplier))
  return `${rendered}${sep}width=${width}&height=${panelHeight}`
}

// Convert a single-panel iframe URL like
//   /d-solo/<uid>/<slug>?orgId=1&panelId=N&theme=light&from=...
// to the full-dashboard "view this panel" URL
//   /d/<uid>/<slug>?orgId=1&viewPanel=N&theme=light&from=...
// which gives the user the time picker, refresh interval, fullscreen,
// and inspect controls.
function toLiveDashboardUrl(src: string): string {
  if (!src.includes("/d-solo/")) return src
  let url: URL
  try {
    url = new URL(src)
  } catch {
    return src
  }
  url.pathname = url.pathname.replace("/d-solo/", "/d/")
  const panelId = url.searchParams.get("panelId")
  if (panelId) {
    url.searchParams.delete("panelId")
    url.searchParams.set("viewPanel", panelId)
  }
  return url.toString()
}

export const GrafanaDefer: QuartzTransformerPlugin<Partial<Options>> = (userOpts) => {
  const opts = { ...defaultOptions, ...userOpts }
  return {
    name: "GrafanaDefer",
    htmlPlugins() {
      return [
        () => {
          return (tree: Root) => {
            visit(tree, "element", (node) => {
              if (
                node.tagName !== "iframe" ||
                !node.properties ||
                typeof node.properties.src !== "string"
              ) {
                return
              }
              const src = node.properties.src
              if (!src.startsWith(opts.matchPrefix)) return

              const heightAttr = node.properties.height
              const heightStr =
                typeof heightAttr === "number"
                  ? String(heightAttr)
                  : typeof heightAttr === "string"
                    ? heightAttr
                    : "300"
              const heightNum = parseInt(heightStr, 10) || 300

              // Title: prefer existing iframe title attr; otherwise empty
              // (component falls back to a generic label).
              const title = typeof node.properties.title === "string" ? node.properties.title : ""

              const renderSrc = toRenderUrl(src, heightNum, opts.widthMultiplier)
              const liveSrc = toLiveDashboardUrl(src)

              node.tagName = "div"
              node.children = []
              node.properties = {
                className: ["grafana-embed"],
                "data-iframe-src": src,
                "data-image-src": renderSrc,
                "data-live-src": liveSrc,
                "data-title": title,
                "data-height": String(heightNum),
                "data-refresh-ms": String(opts.refreshMs),
              }
            })
          }
        },
      ]
    },
  }
}

import test from "node:test"
import assert from "node:assert/strict"
import type { Root, Element } from "hast"
import { GrafanaDefer } from "./grafana-defer"
import publicShares from "./grafana-public-shares.json"

function transform(node: Element): Element {
  const tree: Root = { type: "root", children: [node] }
  const attach = GrafanaDefer().htmlPlugins!({} as never)[0] as () => (tree: Root) => void
  attach()(tree)
  return tree.children[0] as Element
}

test("approved panels retain interactive time selection and the cached PNG path", () => {
  const output = transform({ type: "element", tagName: "iframe", properties: {
    src: "https://graphs.verdify.ai/d-solo/site-home/home?panelId=30&from=now-6h&to=now&orgId=1",
    height: "350",
  }, children: [] })
  assert.equal(output.tagName, "div")
  const url = new URL(String(output.properties["data-iframe-src"]))
  assert.equal(url.pathname, `/public-dashboards/${publicShares["site-home"].token}`)
  assert.equal(url.searchParams.get("panelId"), "30")
  assert.equal(url.searchParams.get("from"), "now-6h")
  assert.equal(url.searchParams.get("height"), "350")
  assert.equal(url.searchParams.has("orgId"), false)
  assert.match(String(output.properties["data-image-src"]), /\/render\/d-solo\/site-home\//)
})

test("full dashboard links use only the approved native share", () => {
  const output = transform({ type: "element", tagName: "a", properties: {
    href: "https://graphs.verdify.ai/d/site-climate/climate?from=now-1d&to=now",
  }, children: [{ type: "text", value: "Climate" }] })
  assert.equal(new URL(String(output.properties.href)).pathname,
    `/public-dashboards/${publicShares["site-climate"].token}`)
})

test("unshared Weather embeds become sign-in links without a public iframe or PNG", () => {
  const output = transform({ type: "element", tagName: "iframe", properties: {
    src: "https://graphs.verdify.ai/d-solo/greenhouse-weather/weather?panelId=20&from=now-24h&to=now",
  }, children: [] })
  assert.equal(output.tagName, "p")
  const link = output.children[0] as Element
  const url = new URL(String(link.properties.href))
  assert.equal(url.pathname, "/d/greenhouse-weather/weather")
  assert.equal(url.searchParams.get("viewPanel"), "20")
  assert.match((link.children[0] as { value: string }).value, /sign-in/)
  assert.equal("data-image-src" in output.properties, false)
})

test("unshared dashboard links retain authentication and never acquire a public token", () => {
  const href = "https://graphs.verdify.ai/d/greenhouse-weather/weather"
  const output = transform({ type: "element", tagName: "a", properties: { href }, children: [] })
  assert.equal(output.properties.href, href)
  assert.equal(output.properties.title, "Requires Grafana sign-in")
})

test("unknown panels in an approved dashboard fail closed", () => {
  assert.throws(() => transform({ type: "element", tagName: "iframe", properties: {
    src: "https://graphs.verdify.ai/d-solo/site-home/home?panelId=999999",
  }, children: [] }), /Unknown saved panel/)
})


test("prototype names are not approved shares", () => {
  const output = transform({ type: "element", tagName: "iframe", properties: {
    src: "https://graphs.verdify.ai/d-solo/__proto__/hidden?panelId=1",
  }, children: [] })
  assert.equal(output.tagName, "p")
  assert.equal("data-iframe-src" in output.properties, false)
})

test("Grafana scene panel keys preserve the approved numeric panel", () => {
  const output = transform({ type: "element", tagName: "a", properties: {
    href: "https://graphs.verdify.ai/d/site-home/home?viewPanel=panel-30",
  }, children: [] })
  assert.equal(new URL(String(output.properties.href)).searchParams.get("panelId"), "30")
})

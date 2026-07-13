import assert from "node:assert/strict"
import test from "node:test"

import {
  generateRSSFeed,
  generateSiteMap,
  type ContentDetails,
  type ContentIndexMap,
} from "./contentIndex"
import { GlobalConfiguration } from "../../cfg"
import { FullSlug } from "../../util/path"

const cfg = {
  pageTitle: "Verdify Lab",
  baseUrl: "lab.verdify.ai",
  locale: "en-US",
} as GlobalConfiguration

function entry(title: string, noindex = false): ContentDetails {
  return {
    slug: "" as FullSlug,
    filePath: "source.md" as ContentDetails["filePath"],
    title,
    links: [],
    tags: [],
    content: title,
    description: `${title} description`,
    date: new Date("2026-07-12T00:00:00.000Z"),
    noindex,
  }
}

test("content indexes omit noindex sources and sitemap missing source folders", () => {
  const index: ContentIndexMap = new Map([
    [
      "data/forecast/index" as FullSlug,
      { ...entry("Forecast"), slug: "data/forecast/index" as FullSlug },
    ],
    ["plans/index" as FullSlug, { ...entry("Plans", true), slug: "plans/index" as FullSlug }],
    [
      "plans/2026-07-12" as FullSlug,
      { ...entry("Plan", true), slug: "plans/2026-07-12" as FullSlug },
    ],
    [
      "private/report" as FullSlug,
      { ...entry("Private report", true), slug: "private/report" as FullSlug },
    ],
    [
      "reference/undated" as FullSlug,
      { ...entry("Undated reference"), slug: "reference/undated" as FullSlug, date: undefined },
    ],
  ])

  const sitemap = generateSiteMap(cfg, index)
  assert.match(sitemap, /https:\/\/lab\.verdify\.ai\/data<\/loc>/)
  assert.match(sitemap, /https:\/\/lab\.verdify\.ai\/data\/forecast\/<\/loc>/)
  assert.doesNotMatch(sitemap, /lab\.verdify\.ai\/plans/)
  assert.doesNotMatch(sitemap, /lab\.verdify\.ai\/private/)
  assert.match(sitemap, /lab\.verdify\.ai\/reference\/undated/)

  const rss = generateRSSFeed(cfg, index, 10)
  assert.match(rss, /lab\.verdify\.ai\/data\/forecast/)
  assert.doesNotMatch(rss, /lab\.verdify\.ai\/plans/)
  assert.doesNotMatch(rss, /lab\.verdify\.ai\/private/)
  assert.doesNotMatch(rss, /lab\.verdify\.ai\/reference\/undated/)
})

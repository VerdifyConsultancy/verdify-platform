import { Root } from "hast"
import { GlobalConfiguration } from "../../cfg"
import { escapeHTML } from "../../util/escape"
import { FilePath, FullSlug, SimpleSlug, joinSegments, simplifySlug } from "../../util/path"
import { QuartzEmitterPlugin } from "../types"
import { toHtml } from "hast-util-to-html"
import { write } from "./helpers"
import { i18n } from "../../i18n"

export type ContentIndexMap = Map<FullSlug, ContentDetails>
export type ContentDetails = {
  slug: FullSlug
  filePath: FilePath
  title: string
  links: SimpleSlug[]
  tags: string[]
  content: string
  richContent?: string
  date?: Date
  description?: string
  noindex: boolean
}

function authoredDate(value: unknown): Date | undefined {
  if (typeof value !== "string" && typeof value !== "number") return undefined
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? undefined : date
}

interface Options {
  enableSiteMap: boolean
  enableRSS: boolean
  rssLimit?: number
  rssFullHtml: boolean
  rssSlug: string
  includeEmptyFiles: boolean
}

const defaultOptions: Options = {
  enableSiteMap: true,
  enableRSS: true,
  rssLimit: 10,
  rssFullHtml: false,
  rssSlug: "index",
  includeEmptyFiles: true,
}

export function generateSiteMap(cfg: GlobalConfiguration, idx: ContentIndexMap): string {
  const base = cfg.baseUrl ?? ""
  const createURLEntry = (slug: SimpleSlug, content: ContentDetails): string => `<url>
    <loc>https://${joinSegments(base, encodeURI(slug))}</loc>
    ${content.date ? `<lastmod>${content.date.toISOString()}</lastmod>` : ""}
  </url>`
  const sourceSlugs = Array.from(idx.keys(), (slug) => simplifySlug(slug))
  const sourceRoutes = new Set(sourceSlugs.map((slug) => slug.replace(/\/$/, "")))
  const generatedFolders = new Set<SimpleSlug>()
  for (const [rawSlug, content] of idx) {
    if (content.noindex) continue
    const slug = simplifySlug(rawSlug)
    const segments = slug.split("/").filter(Boolean)
    for (let depth = 1; depth < segments.length; depth++) {
      const folder = segments.slice(0, depth).join("/") as SimpleSlug
      if (!sourceRoutes.has(folder)) generatedFolders.add(folder)
    }
  }
  const urls = Array.from(idx)
    .filter(([_, content]) => !content.noindex)
    .map(([slug, content]) => createURLEntry(simplifySlug(slug), content))
    .concat(
      Array.from(generatedFolders)
        .sort()
        .map((slug) => createURLEntry(slug, { noindex: false } as ContentDetails)),
    )
    .join("")
  return `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">${urls}</urlset>`
}

export function generateRSSFeed(
  cfg: GlobalConfiguration,
  idx: ContentIndexMap,
  limit?: number,
): string {
  const base = cfg.baseUrl ?? ""

  const createURLEntry = (slug: SimpleSlug, content: ContentDetails): string => `<item>
    <title>${escapeHTML(content.title)}</title>
    <link>https://${joinSegments(base, encodeURI(slug))}</link>
    <guid>https://${joinSegments(base, encodeURI(slug))}</guid>
    <description><![CDATA[ ${content.richContent ?? content.description} ]]></description>
    <pubDate>${content.date?.toUTCString()}</pubDate>
  </item>`

  const items = Array.from(idx)
    .filter(([_, content]) => !content.noindex && content.date)
    .sort(([slug1, f1], [slug2, f2]) => {
      if (f1.date && f2.date) {
        const dateOrder = f2.date.getTime() - f1.date.getTime()
        if (dateOrder !== 0) return dateOrder
      } else if (f1.date && !f2.date) {
        return -1
      } else if (!f1.date && f2.date) {
        return 1
      }

      return simplifySlug(slug1).localeCompare(simplifySlug(slug2))
    })
    .map(([slug, content]) => createURLEntry(simplifySlug(slug), content))
    .slice(0, limit ?? idx.size)
    .join("")

  return `<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
    <channel>
      <title>${escapeHTML(cfg.pageTitle)}</title>
      <link>https://${base}</link>
      <description>${!!limit ? i18n(cfg.locale).pages.rss.lastFewNotes({ count: limit }) : i18n(cfg.locale).pages.rss.recentNotes} on ${escapeHTML(
        cfg.pageTitle,
      )}</description>
      <generator>Verdify public site</generator>
      ${items}
    </channel>
  </rss>`
}

export const ContentIndex: QuartzEmitterPlugin<Partial<Options>> = (opts) => {
  opts = { ...defaultOptions, ...opts }
  return {
    name: "ContentIndex",
    async *emit(ctx, content) {
      const cfg = ctx.cfg.configuration
      const linkIndex: ContentIndexMap = new Map()
      for (const [tree, file] of content) {
        const slug = file.data.slug!
        // Feed and sitemap timestamps are public content, so bind them only to
        // an authored date. Git/filesystem fallback dates change when an
        // immutable snapshot is hydrated and make identical content publish a
        // different index.
        const date = authoredDate(file.data.frontmatter?.date)
        if (opts?.includeEmptyFiles || (file.data.text && file.data.text !== "")) {
          linkIndex.set(slug, {
            slug,
            filePath: file.data.relativePath!,
            title: file.data.frontmatter?.title!,
            links: file.data.links ?? [],
            tags: file.data.frontmatter?.tags ?? [],
            content: file.data.text ?? "",
            richContent: opts?.rssFullHtml
              ? escapeHTML(
                  toHtml(tree as Root, {
                    allowDangerousHtml: true,
                  }),
                )
              : undefined,
            date: date,
            description: file.data.description ?? "",
            noindex: file.data.frontmatter?.noindex === true,
          })
        }
      }

      if (opts?.enableSiteMap) {
        yield write({
          ctx,
          content: generateSiteMap(cfg, linkIndex),
          slug: "sitemap" as FullSlug,
          ext: ".xml",
        })
      }

      if (opts?.enableRSS) {
        yield write({
          ctx,
          content: generateRSSFeed(cfg, linkIndex, opts.rssLimit),
          slug: (opts?.rssSlug ?? "index") as FullSlug,
          ext: ".xml",
        })
      }

      const fp = joinSegments("static", "contentIndex") as FullSlug
      const simplifiedIndex = Object.fromEntries(
        Array.from(linkIndex).map(([slug, content]) => {
          // remove description and from content index as nothing downstream
          // actually uses it. we only keep it in the index as we need it
          // for the RSS feed
          delete content.description
          delete content.date
          delete (content as Partial<ContentDetails>).noindex
          return [slug, content]
        }),
      )

      yield write({
        ctx,
        content: JSON.stringify(simplifiedIndex),
        slug: fp,
        ext: ".json",
      })
    },
    externalResources: (ctx) => {
      if (opts?.enableRSS) {
        return {
          additionalHead: [
            <link
              rel="alternate"
              type="application/rss+xml"
              title="RSS Feed"
              href={`https://${ctx.cfg.configuration.baseUrl}/index.xml`}
            />,
          ],
        }
      }
    },
  }
}

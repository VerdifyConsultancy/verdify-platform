import { PageLayout, SharedLayout } from "./quartz/cfg"
import * as Component from "./quartz/components"

const showsPlanningDate = (page: any) => /^plans\/\d{4}-\d{2}-\d{2}$/.test(page.fileData.slug)

const hasContentH1 = (page: any) =>
  Boolean(
    page.tree?.children?.some((node: any) => node.type === "element" && node.tagName === "h1"),
  )

// components shared across all pages
export const sharedPageComponents: SharedLayout = {
  head: Component.Head(),
  header: [],
  afterBody: [Component.GrafanaEmbeds()],
  footer: Component.Footer({
    links: {
      Home: "/",
      "The Greenhouse": "/greenhouse",
      Climate: "/start/climate",
      Evidence: "/start/evidence",
      Operations: "/data/operations",
      "Planning Archive": "/data/plans",
      "AI Greenhouse": "/start/ai-greenhouse",
      FAQ: "/start/ai-greenhouse/#technical-faq",
      GitHub: "https://github.com/jrvallery/verdify",
      Contact: "/start/contact",
    },
  }),
}

// components for pages that display a single page (e.g. a single note)
export const defaultContentPageLayout: PageLayout = {
  beforeBody: [
    Component.ConditionalRender({
      component: Component.Breadcrumbs({ showCurrentPage: false }),
      condition: (page) => page.fileData.slug !== "index",
    }),
    Component.ConditionalRender({
      component: Component.ArticleTitle(),
      condition: (page) => !hasContentH1(page),
    }),
    Component.ConditionalRender({
      component: Component.ContentMeta({ showDate: true }),
      condition: showsPlanningDate,
    }),
    Component.ConditionalRender({
      component: Component.ContentMeta({ showDate: false }),
      condition: (page) => !showsPlanningDate(page),
    }),
    Component.TagList(),
  ],
  left: [
    Component.PageTitle(),
    Component.MobileOnly(Component.Spacer()),
    Component.Flex({
      components: [
        {
          Component: Component.Search(),
          grow: true,
        },
        { Component: Component.Darkmode() },
        { Component: Component.ReaderMode() },
      ],
    }),
    Component.SiteNav(),
  ],
  right: [],
}

// components for pages that display lists of pages  (e.g. tags or folders)
export const defaultListPageLayout: PageLayout = {
  beforeBody: [
    Component.Breadcrumbs({ showCurrentPage: false }),
    Component.ConditionalRender({
      component: Component.ArticleTitle(),
      condition: (page) => !hasContentH1(page),
    }),
    Component.ContentMeta({ showDate: false }),
  ],
  left: [
    Component.PageTitle(),
    Component.MobileOnly(Component.Spacer()),
    Component.Flex({
      components: [
        {
          Component: Component.Search(),
          grow: true,
        },
      ],
    }),
    Component.SiteNav(),
  ],
  right: [],
}

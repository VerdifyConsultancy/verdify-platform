import { QuartzConfig } from "./quartz/cfg"
import * as Plugin from "./quartz/plugins"

const config: QuartzConfig = {
  configuration: {
    pageTitle: "Verdify Lab",
    pageTitleSuffix: " — Verdify Lab",
    enableSPA: true,
    enablePopovers: true,
    analytics: null,
    locale: "en-US",
    baseUrl: "lab.verdify.ai",
    ignorePatterns: ["private", "templates", ".obsidian"],
    defaultDateType: "modified",
    theme: {
      fontOrigin: "googleFonts",
      cdnCaching: true,
      typography: {
        title: { name: "Inter", weights: [600, 700] },
        header: { name: "Inter", weights: [500, 600, 700] },
        body: {
          name: "Inter",
          weights: [400, 500, 600],
          includeItalic: true,
        },
        code: { name: "IBM Plex Mono", weights: [400, 500, 600] },
      },
      colors: {
        lightMode: {
          light: "#F4F7F4",
          lightgray: "#DCEDE7",
          gray: "#6B7280",
          darkgray: "#2A3437",
          dark: "#112231",
          secondary: "#0E5A43",
          tertiary: "#2E7D5C",
          highlight: "rgba(14, 90, 67, 0.10)",
          textHighlight: "#DCEDE799",
        },
        darkMode: {
          light: "#F4F7F4",
          lightgray: "#DCEDE7",
          gray: "#6B7280",
          darkgray: "#2A3437",
          dark: "#112231",
          secondary: "#0E5A43",
          tertiary: "#2E7D5C",
          highlight: "rgba(14, 90, 67, 0.10)",
          textHighlight: "#DCEDE799",
        },
      },
    },
  },
  plugins: {
    transformers: [
      Plugin.FrontMatter(),
      Plugin.CreatedModifiedDate({
        priority: ["frontmatter", "git", "filesystem"],
      }),
      Plugin.SyntaxHighlighting({
        theme: {
          light: "github-light",
          dark: "github-light",
        },
        keepBackground: false,
      }),
      Plugin.ObsidianFlavoredMarkdown({ enableInHtmlEmbed: true }),
      Plugin.GitHubFlavoredMarkdown(),
      Plugin.TableOfContents(),
      Plugin.CrawlLinks({
        markdownLinkResolution: "shortest",
        lazyLoad: true,
      }),
      Plugin.GrafanaDefer(),
      Plugin.Description(),
      Plugin.Latex({ renderEngine: "katex" }),
    ],
    filters: [Plugin.RemoveDrafts()],
    emitters: [
      Plugin.AliasRedirects(),
      Plugin.ComponentResources(),
      Plugin.ContentPage(),
      Plugin.FolderPage(),
      Plugin.TagPage(),
      Plugin.ContentIndex({
        enableSiteMap: true,
        enableRSS: true,
      }),
      Plugin.Assets(),
      Plugin.Static(),
      Plugin.RobotsTxt(),
      Plugin.Favicon(),
      Plugin.NotFoundPage(),
    ],
  },
}

export default config

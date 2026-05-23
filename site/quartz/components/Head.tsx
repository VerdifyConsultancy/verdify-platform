import { i18n } from "../i18n"
import { FullSlug, getFileExtension, joinSegments, pathToRoot } from "../util/path"
import { CSSResourceToStyleElement, JSResourceToScriptElement } from "../util/resources"
import { googleFontHref, googleFontSubsetHref } from "../util/theme"
import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { unescapeHTML } from "../util/escape"
import { CustomOgImagesEmitterName } from "../plugins/emitters/ogImage"
export default (() => {
  const Head: QuartzComponent = ({
    cfg,
    fileData,
    externalResources,
    ctx,
  }: QuartzComponentProps) => {
    const titleSuffix = cfg.pageTitleSuffix ?? ""
    const title =
      (fileData.frontmatter?.title ?? i18n(cfg.locale).propertyDefaults.title) + titleSuffix
    const description =
      fileData.frontmatter?.socialDescription ??
      fileData.frontmatter?.description ??
      unescapeHTML(fileData.description?.trim() ?? i18n(cfg.locale).propertyDefaults.description)
    const noindex =
      fileData.slug?.startsWith("tags/") ||
      fileData.frontmatter?.noindex === true ||
      fileData.frontmatter?.noindex === "true"

    const { css, js, additionalHead } = externalResources

    const url = new URL(`https://${cfg.baseUrl ?? "example.com"}`)
    const path = url.pathname as FullSlug
    const baseDir = fileData.slug === "404" ? path : pathToRoot(fileData.slug!)
    const iconPath = joinSegments(baseDir, "static/brand/favicon.svg")
    const appleTouchIconPath = joinSegments(baseDir, "static/brand/apple-touch-icon.png")

    // Url of current page
    const socialSlug =
      fileData.slug === "index" ? "" : (fileData.slug?.replace(/\/index$/, "") ?? "")
    const socialUrl =
      fileData.slug === "404" || socialSlug === ""
        ? url.toString()
        : joinSegments(url.toString(), socialSlug)

    const usesCustomOgImage = ctx.cfg.plugins.emitters.some(
      (e) => e.name === CustomOgImagesEmitterName,
    )
    const socialImage = fileData.frontmatter?.socialImage
    const socialImagePath =
      typeof socialImage === "string" && socialImage.length > 0
        ? socialImage.startsWith("http")
          ? socialImage
          : `https://${cfg.baseUrl}${socialImage.startsWith("/") ? socialImage : `/${socialImage}`}`
        : undefined
    const ogImageDefaultPath = socialImagePath ?? `https://${cfg.baseUrl}/static/og-image-v2.jpg`
    const usesDefaultOgImage = ogImageDefaultPath.endsWith("/static/og-image-v2.jpg")
    const structuredData = {
      "@context": "https://schema.org",
      "@graph": [
        {
          "@type": "WebSite",
          "@id": `https://${cfg.baseUrl}/#website`,
          name: "Verdify Lab",
          url: `https://${cfg.baseUrl}`,
          description:
            "An ESP32-run greenhouse where an AI planning agent writes bounded tactical plans, while telemetry, costs, failures, and lessons stay public.",
        },
        {
          "@type": "WebPage",
          name: title,
          url: socialUrl,
          description,
          isPartOf: {
            "@id": `https://${cfg.baseUrl}/#website`,
          },
        },
      ],
    }

    return (
      <head>
        <title>{title}</title>
        <meta charSet="utf-8" />
        {cfg.theme.cdnCaching && cfg.theme.fontOrigin === "googleFonts" && (
          <>
            <link rel="preconnect" href="https://fonts.googleapis.com" />
            <link rel="preconnect" href="https://fonts.gstatic.com" />
            <link rel="stylesheet" href={googleFontHref(cfg.theme)} />
            {cfg.theme.typography.title && (
              <link rel="stylesheet" href={googleFontSubsetHref(cfg.theme, cfg.pageTitle)} />
            )}
          </>
        )}
        <link rel="preconnect" href="https://cdnjs.cloudflare.com" crossOrigin="anonymous" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />

        <meta name="og:site_name" content={cfg.pageTitle}></meta>
        <meta property="og:title" content={title} />
        <meta property="og:type" content="website" />
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:title" content={title} />
        <meta name="twitter:description" content={description} />
        <meta property="og:description" content={description} />
        <meta property="og:image:alt" content={description} />

        {!usesCustomOgImage && (
          <>
            <meta property="og:image" content={ogImageDefaultPath} />
            <meta property="og:image:url" content={ogImageDefaultPath} />
            <meta property="og:image:secure_url" content={ogImageDefaultPath} />
            <meta name="twitter:image" content={ogImageDefaultPath} />
            <meta
              property="og:image:type"
              content={`image/${getFileExtension(ogImageDefaultPath) ?? "png"}`}
            />
            {usesDefaultOgImage && (
              <>
                <meta property="og:image:width" content="1200" />
                <meta property="og:image:height" content="630" />
              </>
            )}
          </>
        )}

        {cfg.baseUrl && (
          <>
            <meta property="twitter:domain" content={cfg.baseUrl}></meta>
            <meta property="og:url" content={socialUrl}></meta>
            <meta property="twitter:url" content={socialUrl}></meta>
          </>
        )}

        <link rel="icon" href={iconPath} type="image/svg+xml" />
        <link rel="apple-touch-icon" href={appleTouchIconPath} />
        <link rel="canonical" href={socialUrl} />
        <meta name="description" content={description} />
        {noindex && <meta name="robots" content="noindex, follow" />}
        <script
          dangerouslySetInnerHTML={{
            __html: `(()=>{const apply=()=>{document.documentElement.setAttribute("saved-theme","light");try{localStorage.setItem("theme","light")}catch{}};apply();if(!window.__verdifyLightThemeOnly){window.__verdifyLightThemeOnly=true;document.addEventListener("nav",apply)}})();`,
          }}
        />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify(structuredData),
          }}
        />
        <script async src="https://www.googletagmanager.com/gtag/js?id=G-74QKK65Q20" />
        <script
          dangerouslySetInnerHTML={{
            __html: `window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-74QKK65Q20',{send_page_view:false});gtag('event','page_view',{page_title:document.title,page_location:location.href});document.addEventListener('nav',function(){gtag('event','page_view',{page_title:document.title,page_location:location.href});});`,
          }}
        />

        {css.map((resource) => CSSResourceToStyleElement(resource, true))}
        {js
          .filter((resource) => resource.loadTime === "beforeDOMReady")
          .map((res) => JSResourceToScriptElement(res, true))}
        {additionalHead.map((resource) => {
          if (typeof resource === "function") {
            return resource(fileData)
          } else {
            return resource
          }
        })}
      </head>
    )
  }

  return Head
}) satisfies QuartzComponentConstructor

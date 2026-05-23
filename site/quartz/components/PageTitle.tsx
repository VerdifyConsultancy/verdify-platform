import { joinSegments, pathToRoot } from "../util/path"
import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { classNames } from "../util/lang"
import { i18n } from "../i18n"

const PageTitle: QuartzComponent = ({ fileData, cfg, displayClass }: QuartzComponentProps) => {
  const title = cfg?.pageTitle ?? i18n(cfg.locale).propertyDefaults.title
  const baseDir = pathToRoot(fileData.slug!)
  const logoPath = joinSegments(baseDir, "static/brand/verdify-wordmark.svg")
  return (
    <h2 class={classNames(displayClass, "page-title")}>
      <a href={baseDir} aria-label={title}>
        <img class="page-title__logo" src={logoPath} alt="Verdify" />
        <span class="page-title__label">Lab</span>
      </a>
    </h2>
  )
}

PageTitle.css = `
.page-title {
  font-size: 1.1rem;
  margin: 0;
  font-family: var(--titleFont);
}

.page-title a {
  align-items: center;
  color: var(--dark);
  display: flex;
  gap: 0.5rem;
  line-height: 1;
  min-width: 0;
  text-decoration: none;
}

.page-title__logo {
  display: block;
  height: 2.35rem;
  margin: 0;
  max-width: 9.5rem;
  object-fit: contain;
  width: auto;
}

.page-title__label {
  border-left: 1px solid var(--lightgray);
  color: var(--gray);
  font-family: var(--bodyFont);
  font-size: 0.76rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  padding-left: 0.55rem;
  text-transform: uppercase;
}
`

export default (() => PageTitle) satisfies QuartzComponentConstructor

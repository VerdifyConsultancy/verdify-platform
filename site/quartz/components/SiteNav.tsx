import {
    QuartzComponent,
    QuartzComponentConstructor,
    QuartzComponentProps,
} from "./types";
import style from "./styles/siteNav.scss";

type SiteNavItem = {
    label: string;
    href?: string;
    slug?: string;
    aliases?: string[];
    activePrefixes?: string[];
    exact?: boolean;
    children?: SiteNavItem[];
};

type SiteNavGroup = {
    title: string;
    links: SiteNavItem[];
};

type SiteNavModel = {
    groups: SiteNavGroup[];
};

type SiteNavItemOptions = Pick<
    SiteNavItem,
    "activePrefixes" | "aliases" | "exact"
>;

function normalizeSlug(slug: string | undefined) {
    return (slug ?? "").replace(/\/index$/, "").replace(/^\//, "");
}

function routeForSlug(slug: string) {
    const normalized = normalizeSlug(slug);
    return normalized === "index" || normalized === "" ? "/" : `/${normalized}`;
}

function pageLink(
    label: string,
    slug: string,
    options: SiteNavItemOptions = {},
): SiteNavItem {
    return {
        label,
        href: routeForSlug(slug),
        slug: normalizeSlug(slug),
        ...options,
    };
}

function latestPlanLink(
    allFiles: QuartzComponentProps["allFiles"],
): SiteNavItem {
    const slug = allFiles
        .map((file) => normalizeSlug(file.slug))
        .filter((fileSlug) => /^plans\/\d{4}-\d{2}-\d{2}$/.test(fileSlug))
        .sort((a, b) => b.localeCompare(a))[0];

    if (!slug) {
        return pageLink("Latest Plan", "data/plans", { exact: true });
    }

    return {
        label: "Latest Plan",
        href: routeForSlug(slug),
        slug,
        exact: true,
    };
}

function buildNav(allFiles: QuartzComponentProps["allFiles"]): SiteNavModel {
    return {
        groups: [
            {
                title: "Overview",
                links: [
                    pageLink("Home", "index", { exact: true }),
                    pageLink("AI Greenhouse", "start/ai-greenhouse"),
                    latestPlanLink(allFiles),
                    pageLink("Scorecard", "data/planning-quality"),
                    pageLink("Resource Use", "start/resource-use"),
                    pageLink("Lighting", "greenhouse/lighting"),
                    pageLink("Hydroponics", "greenhouse/hydroponics"),
                    pageLink("Irrigation", "greenhouse/irrigation"),
                    pageLink("Soil Sensors", "greenhouse/soil"),
                    pageLink("Evidence", "start/evidence"),
                    pageLink("Architecture", "reference/architecture"),
                    pageLink("Contact", "start/contact", { exact: true }),
                    {
                        label: "Consulting",
                        href: "https://www.verdify.ai/",
                    },
                    pageLink("About", "start/about", { exact: true }),
                ],
            },
            {
                title: "Live Evidence",
                links: [
                    pageLink("Operations", "data/operations"),
                    pageLink("Climate", "start/climate"),
                    pageLink("Forecast", "data/forecast", { exact: true }),
                    pageLink("Planning Archive", "data/plans", {
                        aliases: ["plans"],
                        exact: true,
                    }),
                ],
            },
            {
                title: "Greenhouse",
                links: [
                    pageLink("Greenhouse Tour", "greenhouse", { exact: true }),
                    pageLink("Irrigation", "greenhouse/irrigation"),
                    pageLink("Equipment", "greenhouse/equipment"),
                    pageLink("Structure", "greenhouse/structure"),
                    pageLink("Crops", "greenhouse/crops"),
                    pageLink("Zones", "greenhouse/zones"),
                ],
            },
            {
                title: "Reference",
                links: [
                    pageLink(
                        "Baseline vs AI Planning Agent",
                        "data/baseline-vs-iris",
                    ),
                    pageLink("Planning Loop", "reference/planning-loop"),
                    pageLink(
                        "Planner Contract and AI Tunables",
                        "reference/ai-tunables",
                        { aliases: ["reference/planner-contract"] },
                    ),
                    pageLink("Lessons", "reference/lessons"),
                    pageLink("Safety", "reference/safety"),
                    pageLink("Data Model", "reference/data-model"),
                    pageLink("Related Work", "reference/related-work"),
                    {
                        label: "Case Study",
                        href: "https://www.verdify.ai/proof/public-ai-greenhouse/",
                    },
                    {
                        label: "GitHub",
                        href: "https://github.com/jrvallery/verdify",
                    },
                ],
            },
        ],
    };
}

function normalizeTargets(targets: string[] | undefined) {
    return (targets ?? []).map(normalizeSlug).filter(Boolean);
}

function isActiveTarget(current: string, target: string) {
    if (target === "index") {
        return current === "" || current === "index";
    }
    return current === target || current.startsWith(`${target}/`);
}

function isActiveItem(currentSlug: string, item: SiteNavItem): boolean {
    if (!item.slug) return false;
    const current = normalizeSlug(currentSlug);
    const targets = normalizeTargets([item.slug, ...(item.aliases ?? [])]);
    const activePrefixes = normalizeTargets(item.activePrefixes);

    if (targets.includes("index")) {
        return current === "" || current === "index";
    }
    if (targets.some((target) => current === target)) {
        return true;
    }
    if (activePrefixes.some((target) => isActiveTarget(current, target))) {
        return true;
    }
    if (item.exact) {
        return false;
    }
    return targets.some((target) => isActiveTarget(current, target));
}

function isActiveBranch(currentSlug: string, item: SiteNavItem): boolean {
    return (
        isActiveItem(currentSlug, item) ||
        Boolean(
            item.children?.some((child) => isActiveBranch(currentSlug, child)),
        )
    );
}

function activeSectionLabel(currentSlug: string, model: SiteNavModel) {
    return (
        model.groups.find((group) =>
            group.links.some((link) => isActiveBranch(currentSlug, link)),
        )?.title ?? "Menu"
    );
}

function renderItem(currentSlug: string, item: SiteNavItem, child = false) {
    const isActive = isActiveItem(currentSlug, item);
    const branchActive = isActiveBranch(currentSlug, item);

    if (item.children?.length) {
        return (
            <li
                class={
                    child
                        ? "site-nav__child site-nav__branch-item"
                        : "site-nav__item site-nav__branch-item"
                }
            >
                <details class="site-nav__branch" open={branchActive}>
                    <summary>
                        <span>{item.label}</span>
                        <span
                            class="site-nav__chevron"
                            aria-hidden="true"
                        ></span>
                    </summary>
                    <ul class="site-nav__children">
                        {item.children.map((nested) =>
                            renderItem(currentSlug, nested, true),
                        )}
                    </ul>
                </details>
            </li>
        );
    }

    return (
        <li class={child ? "site-nav__child" : "site-nav__item"}>
            <a href={item.href ?? "#"} class={isActive ? "active" : undefined}>
                {item.label}
            </a>
        </li>
    );
}

function renderNavPanel(
    currentSlug: string,
    _idPrefix: string,
    model: SiteNavModel,
) {
    return (
        <div class="site-nav__panel">
            {model.groups.map((group) => (
                <details class="site-nav__group" open>
                    <summary>
                        <span>{group.title}</span>
                        <span
                            class="site-nav__chevron"
                            aria-hidden="true"
                        ></span>
                    </summary>
                    <ul>
                        {group.links.map((link) =>
                            renderItem(currentSlug, link),
                        )}
                    </ul>
                </details>
            ))}
        </div>
    );
}

const SiteNav: QuartzComponent = ({
    fileData,
    allFiles,
}: QuartzComponentProps) => {
    const currentSlug = fileData.slug ?? "";
    const model = buildNav(allFiles);
    const label = activeSectionLabel(currentSlug, model);

    return (
        <nav class="site-nav" aria-label="Site navigation">
            <div class="site-nav__desktop">
                {renderNavPanel(currentSlug, "site-nav-desktop", model)}
            </div>
            <details class="site-nav__mobile">
                <summary>
                    <span class="site-nav__menu-label">Menu</span>
                    <span class="site-nav__menu-current">{label}</span>
                    <span class="site-nav__chevron" aria-hidden="true"></span>
                </summary>
                {renderNavPanel(currentSlug, "site-nav-mobile", model)}
            </details>
        </nav>
    );
};

SiteNav.css = style;
SiteNav.afterDOMLoaded = `
document.addEventListener("click", (event) => {
  const target = event.target
  if (!(target instanceof Element)) return
  const link = target.closest(".site-nav__mobile a")
  if (!link) return
  const mobileNav = document.querySelector(".site-nav__mobile")
  if (mobileNav instanceof HTMLDetailsElement) mobileNav.open = false
})

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return
  const mobileNav = document.querySelector(".site-nav__mobile")
  if (mobileNav instanceof HTMLDetailsElement) mobileNav.open = false
})
`;

export default (() => SiteNav) satisfies QuartzComponentConstructor;

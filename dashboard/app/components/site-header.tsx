// SPDX-License-Identifier: MIT
import { useLayoutEffect, useRef, type RefObject } from "react";
import { Link, useLocation } from "react-router";
import { SECTIONS, sectionFor, type Section } from "../lib/sections";
import { useShellStatus, type AlertSeverity } from "../lib/shell";
import { AccountMenu } from "./account-menu";
import { Banner } from "./banner";
import { SectionIcon } from "./icons";

export { RolePill, SignOutForm } from "./account-menu";

/**
 * The V of the lockup: the `verdify-v-glyph` group of site/quartz/static/brand/verdify-wordmark.svg (leaf, ink stroke
 * and mint dot), without its style and text, coloured by `.brand-leaf`, `.brand-mark` and `.brand-dot`. Never animated.
 */
export function BrandGlyph() {
  return <svg className="brand-glyph" viewBox="30 22 100 127" aria-hidden="true" focusable="false">
    <g transform="translate(-10.500,4.200) scale(0.300000,0.400000)">
      <path className="brand-leaf" d="M410 98 C416 99 419 106 415 114 L301 318 C291 334 276 339 261 336 C252 334 246 328 244 320 C279 262 337 172 396 106 C400 101 405 97 410 98Z" />
      <path className="brand-mark" d="M160 196 C172 194 181 199 187 209 L264 322 C270 328 278 332 292 325 C286 337 276 347 263 352 C245 360 225 351 212 338 L142 237 C135 227 139 213 147 205 C152 200 156 197 160 196Z" />
      <circle className="brand-dot" cx="444.0" cy="68.0" r="21.0" />
    </g>
  </svg>;
}

/**
 * The lockup: the glyph is the V, so "erdify" follows it with no gap (on its baseline, kerned as the wordmark sets it),
 * then " · DASHBOARD". `reloadDocument` makes it a full page load, for the pages outside the shell.
 */
export function Brand({ reloadDocument = false }: { reloadDocument?: boolean }) {
  return <Link className="brand" to="/" reloadDocument={reloadDocument} aria-label="Verdify dashboard, home"><span className="brand-word"><BrandGlyph /><span>erdify<small> · Dashboard</small></span></span></Link>;
}

/**
 * The one open-alert count after "Activity" (redesign §2.2): API-02 `alerts.open` as a pill filled by the worst open
 * severity (critical `--fault`, warning `--tint-warn`, info `--field`). Nothing until API-02 answers, and nothing while
 * the API is down, when the last count is old news. Zero draws no pill, so the quiet state is quiet; a screen reader
 * still hears "0 open alerts", and "N open alerts" otherwise.
 */
export function CountPill({ count, severity }: { count: number | null; severity: AlertSeverity | null }) {
  if (count === null) return null;
  return <>{" "}{count > 0 ? <span className="nav-count" data-severity={severity ?? "info"}>{count}</span> : <span className="screen-reader">{count}</span>}<span className="screen-reader"> open alerts</span></>;
}

function useCurrentSection(): Section | null {
  return sectionFor(useLocation().pathname);
}

/**
 * Keeps `--shell-header-h` on the document at the sticky header's height, fault row included, so a row that sticks
 * under it (Water's tabs at phone width) sits right below it. Set through CSSOM, never a style attribute.
 */
function useHeaderHeight(ref: RefObject<HTMLElement | null>) {
  useLayoutEffect(() => {
    const header = ref.current;
    if (!header || typeof ResizeObserver !== "function") return;
    const root = header.ownerDocument.documentElement;
    const update = () => root.style.setProperty("--shell-header-h", `${Math.ceil(header.getBoundingClientRect().height)}px`);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(header);
    return () => { observer.disconnect(); root.style.removeProperty("--shell-header-h"); };
  }, [ref]);
}

/**
 * Weather's sticky header with the Verdify lockup (redesign §2.2): the seven sections with the 2 px forest underline
 * on the current one and the count after Activity, then the account button. The one fault banner, worst first, is a
 * row inside the header while it is true, so it never scrolls away. At 860 px and below the stylesheet keeps the lockup
 * and the account button, and `BottomNav` takes over the sections. Nothing else goes here: no Stop, no freshness, no
 * search (D-UX-11).
 */
export function SiteHeader() {
  const current = useCurrentSection();
  const { openAlerts, alertSeverity, fault } = useShellStatus();
  const header = useRef<HTMLElement>(null);
  useHeaderHeight(header);
  return <header ref={header} className="site-header">
    <div className="shell">
      <Brand />
      <nav className="primary-nav" aria-label="Sections">
        {SECTIONS.map((section) => <Link key={section.id} to={section.to} aria-current={current?.id === section.id ? "page" : undefined}>
          {section.label}{section.id === "activity" && <CountPill count={openAlerts} severity={alertSeverity} />}
        </Link>)}
      </nav>
      <AccountMenu />
    </div>
    {fault && <div className="header-fault"><div className="shell"><Banner tone="fault" link={fault.link}>{fault.text}</Banner></div></div>}
  </header>;
}

/**
 * The phone's fixed bottom bar (860 px and below, redesign §2.3): the same seven sections in the same order, each a
 * seventh of the width and at least 44 px, an icon over a one-word label; the count rides the Activity icon's corner. The
 * label comes first in the markup, so the link reads "Activity 2 open alerts".
 */
export function BottomNav() {
  const current = useCurrentSection();
  const { openAlerts, alertSeverity } = useShellStatus();
  return <nav className="bottom-nav" aria-label="Sections, phone">
    {SECTIONS.map((section) => <Link key={section.id} className="tab" to={section.to} aria-current={current?.id === section.id ? "page" : undefined}>
      <SectionIcon section={section.id} className="tab-icon" />
      <span className="tab-label">{section.label}</span>
      {section.id === "activity" && <CountPill count={openAlerts} severity={alertSeverity} />}
    </Link>)}
  </nav>;
}

/**
 * The header of the sign-in and error pages: the lockup alone, with no nav and no tools. Its link is a full page load:
 * the root's data (a sign-in refusal) never revalidates on a client navigation, so a client navigation from a refusal
 * card would draw the same card at /, and the BFF must see the request to start a sign-in.
 */
export function PlainHeader() {
  return <header className="site-header"><div className="shell"><Brand reloadDocument /></div></header>;
}

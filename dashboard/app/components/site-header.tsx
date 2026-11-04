// SPDX-License-Identifier: MIT
import { Link, useLocation } from "react-router";
import { useMe, useShellStatus } from "../lib/shell";

type Section = { readonly label: string; readonly to: string; readonly owns: (path: string) => boolean };
const under = (base: string) => (path: string) => path === base || path.startsWith(`${base}/`);

/** Sections and the paths each one owns (weather's `SECTIONS` model): `/graphs/scorecard` belongs to Graphs. */
export const SECTIONS: readonly Section[] = [
  { label: "Now", to: "/", owns: (path: string) => path === "/" },
  { label: "Graphs", to: "/graphs", owns: under("/graphs") },
  { label: "Plan", to: "/plan", owns: under("/plan") },
  { label: "Water", to: "/water", owns: under("/water") },
  { label: "Settings", to: "/settings", owns: under("/settings") },
  { label: "Activity", to: "/activity", owns: under("/activity") },
  { label: "Equipment", to: "/equipment", owns: under("/equipment") },
];
const section = (to: string) => SECTIONS.find((item) => item.to === to)!;
/** What `/more` lists on a phone, where the bottom bar has no room for them. */
export const MORE_SECTIONS: readonly Section[] = [section("/plan"), section("/settings"), section("/equipment")];
/** The phone's bottom bar: More owns `/more` and every section it lists. */
export const TABS: readonly Section[] = [
  section("/"), section("/water"), section("/graphs"), section("/activity"),
  { label: "More", to: "/more", owns: (path: string) => under("/more")(path) || MORE_SECTIONS.some((item) => item.owns(path)) },
];

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

/** "Activity 2": the one open-alert count (API-02 `alerts.open`) as plain text, once API-02 has answered. */
function SectionLabel({ item, count }: { item: Section; count: number | null }) {
  if (item.to !== "/activity" || count === null) return <>{item.label}</>;
  return <>{item.label} <span className="nav-count">{count}</span><span className="screen-reader"> open alerts</span></>;
}

/** The role from API-01: OPERATOR or VIEWER (the stylesheet uppercases it). */
export function RolePill({ role }: { role: string }) {
  const operator = role === "operator";
  return <span className="tag role-pill" data-role={operator ? "operator" : "viewer"}>{operator ? "Operator" : "Viewer"}</span>;
}

/** Sign out is a plain POST form: the BFF checks its exact Origin, deletes the session and ends at Authentik (#1141). */
export function SignOutForm({ className }: { className?: string }) {
  return <form className={className ? `signout-form ${className}` : "signout-form"} method="post" action="/auth/logout"><button type="submit">Sign out</button></form>;
}

/**
 * Weather's header (`site-header.tsx`) with the Verdify lockup and no units toggle. Above 860 px: every section with
 * the open-alert count, then the role pill, the display name and Sign out. At 860 px and below the stylesheet keeps
 * the lockup and the pill, and `BottomNav` takes over the sections.
 */
export function SiteHeader() {
  const { pathname } = useLocation();
  const count = useShellStatus().openAlerts;
  const me = useMe();
  const name = me ? me.display_name || me.username : null;
  return <header className="site-header"><div className="shell">
    <Brand />
    <nav className="primary-nav" aria-label="Sections">
      {SECTIONS.map((item) => <Link key={item.to} to={item.to} aria-current={item.owns(pathname) ? "page" : undefined}>{item.to === "/plan" ? null : <SectionLabel item={item} count={count} />}</Link>)}
    </nav>
    <div className="header-tools">
      {me && <RolePill role={me.role} />}
      {name && <span className="header-name">{name}</span>}
      <SignOutForm className="header-signout" />
    </div>
  </div></header>;
}

/** The phone's fixed bottom bar (860 px and below): Now, Water, Graphs, Activity with the count, and More. */
export function BottomNav() {
  const { pathname } = useLocation();
  const count = useShellStatus().openAlerts;
  return <nav className="bottom-nav" aria-label="Sections, phone">
    {TABS.map((item) => <Link key={item.to} to={item.to} aria-current={item.owns(pathname) ? "page" : undefined}><SectionLabel item={item} count={count} /></Link>)}
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

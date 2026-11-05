// SPDX-License-Identifier: MIT
import { useEffect, useId, useRef, useState, type ReactNode, type Ref } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { Outlet, useLocation, useMatches } from "react-router";
import { SIGN_IN_REFUSALS, type AuthError } from "../lib/page-context";
import { SessionProvider } from "../lib/session";
import { ShellContext, createQueryClient, revisionLabel, signInHref, useShell, useShellPolls, useShellStatus } from "../lib/shell";
import { PollingPaused } from "../lib/use-current";
import { Banner } from "./banner";
import { cls } from "./common";
import { ServerClockProvider } from "./freshness";
import { BottomNav, PlainHeader, SignOutForm, SiteHeader } from "./site-header";
import { ToastProvider } from "./toast";

/**
 * Runs the shell's two polls once for the page and provides what they say: the shell state (`useMe()`,
 * `useShellNow()`, `useShellStatus()`), the session (role and `writes_enabled` from API-01, `apiDown` from API-02;
 * a viewer with changes off until API-01 answers) and the server clock for every age on the page.
 */
export function ShellProvider({ build, children }: { build: string; children?: ReactNode }) {
  const { skewMs, ...shell } = useShellPolls(build);
  const me = shell.me.data;
  return <ShellContext.Provider value={shell}>
    <SessionProvider role={me?.role === "operator" ? "operator" : "viewer"} writesEnabled={me?.writes_enabled === true} apiDown={shell.status.apiDown}>
      <ServerClockProvider skewMs={skewMs}>{children}</ServerClockProvider>
    </SessionProvider>
  </ShellContext.Provider>;
}

/**
 * The warn banners, one line each, in flow at the top of `<main>` above the page header, only while true (redesign
 * §2.5). The one fault banner is not here: it is a row inside the sticky header (`SiteHeader`), so it never scrolls away.
 */
export function ShellNotices() {
  const { warns } = useShellStatus();
  if (!warns.length) return null;
  return <div className="banners">
    {warns.map((banner) => <Banner key={banner.kind} tone={banner.tone} link={banner.link}>{banner.text}</Banner>)}
  </div>;
}

/**
 * What a route module may say about its page through `export const handle`: `wide` gives it `--shell-wide` (96rem)
 * instead of the 80rem shell, for charts and timelines (Graphs and Water → Schedules, redesign §3.2).
 */
export type PageHandle = { readonly wide?: boolean };
const isWide = (handle: unknown) => typeof handle === "object" && handle !== null && (handle as PageHandle).wide === true;
/** True when the deepest route that says asks for the wide page. */
export function useWidePage(): boolean {
  const matches = useMatches();
  return matches.some((match) => isWide(match.handle));
}

/** "Verdify dashboard · build <12 hex> · API <12 hex>"; the API's build reads "—" until API-01 answers. */
export function BuildLine({ build, api }: { build: string; api: string | null }) {
  return <span className="build-line">Verdify dashboard · build <span className="mono">{revisionLabel(build)}</span> · API <span className="mono">{revisionLabel(api)}</span></span>;
}

/**
 * Weather's `.site-footer`: the build line, the time zone every time on the site is in (stated once here, on the chart
 * axes and in every `<time title>`, D-UX-08), the two sibling sites and the IBM Plex licences.
 */
export function SiteFooter({ build, api }: { build: string; api: string | null }) {
  return <footer className="site-footer"><div className="shell">
    <BuildLine build={build} api={api} />
    <span className="footer-zone">Times are America/Denver</span>
    <a href="https://graphs.verdify.ai/">graphs.verdify.ai<span aria-hidden="true"> ↗</span></a>
    <a href="https://lab.verdify.ai/">lab.verdify.ai<span aria-hidden="true"> ↗</span></a>
    <p className="small"><a href="/fonts/OFL-sans.txt">IBM Plex Sans license</a> · <a href="/fonts/OFL-mono.txt">IBM Plex Mono license</a></p>
  </div></footer>;
}
function ShellFooter() {
  const shell = useShell();
  return <SiteFooter build={shell.build} api={shell.me.data?.source_revision ?? null} />;
}

/** One card with one heading: the sign-in and error pages, and the session-ended card. */
export function PageCard({ title, level = 1, id, children }: { title: ReactNode; level?: 1 | 2; id?: string; children?: ReactNode }) {
  const Heading = level === 1 ? "h1" : "h2";
  return <div className="card page-card"><Heading id={id}>{title}</Heading>{children}</div>;
}

/**
 * Sign in at Authentik, then back to `returnTo` (the BFF's default, `/`, without it). A plain link: the BFF answers
 * `/auth/login` itself, so this is a full page load, never a client navigation.
 */
export function SignInLink({ returnTo, linkRef }: { returnTo?: string; linkRef?: Ref<HTMLAnchorElement> }) {
  return <a ref={linkRef} className="button-link btn-primary" href={returnTo === undefined ? "/auth/login" : signInHref(returnTo)}>Sign in</a>;
}

/**
 * A poll or a write got 401 (the 12 h session ended or was removed): a card over the current page whose Sign in returns
 * to the same path and query. The page behind it is inert, and every poll has stopped.
 */
export function SessionEndedCard() {
  const { pathname, search } = useLocation();
  const link = useRef<HTMLAnchorElement>(null);
  const id = useId();
  useEffect(() => { link.current?.focus(); }, []);
  return <div className="session-ended" role="alertdialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-text`}>
    <PageCard title="Your session ended" level={2} id={`${id}-title`}>
      <p id={`${id}-text`}>Sign in again.</p>
      <div className="actions"><SignInLink returnTo={`${pathname}${search}`} linkRef={link} /></div>
    </PageCard>
  </div>;
}

/** SCR-09 for a sign-in the BFF refused: the header without nav or tools and one card, at the same URL and status. */
export function SignInRefusalPage({ build, refusal }: { build: string; refusal: AuthError }) {
  const card = SIGN_IN_REFUSALS[refusal.reason];
  return <>
    <PlainHeader />
    <main id="main-content" className="shell page-card-main" tabIndex={-1}>
      <PageCard title={card.title}>
        <p>{card.text}</p>
        <div className="actions">{card.action === "sign_in" ? <SignInLink /> : <SignOutForm />}</div>
        {refusal.requestId && <p className="small">Request id <span className="mono">{refusal.requestId}</span></p>}
      </PageCard>
    </main>
    <SiteFooter build={build} api={null} />
  </>;
}

/**
 * The page around every screen: the header with its nav, account menu and fault row, the warn notices, the screen,
 * the footer, the phone bar and the toasts.
 * It owns the page's query client; a `SessionEnded` from any query or mutation raises the session-ended card. A sign-in
 * refusal (`authError`) renders SCR-09's card in place of the screen, with no polls (there is no session to poll with).
 */
export function AppShell({ build, authError }: { build: string; authError: AuthError | null }) {
  const [ended, setEnded] = useState(false);
  const [client] = useState(() => createQueryClient(() => setEnded(true)));
  const wide = useWidePage();
  if (authError) return <SignInRefusalPage build={build} refusal={authError} />;
  return <QueryClientProvider client={client}><PollingPaused.Provider value={ended}><ShellProvider build={build}><ToastProvider>
    <div className="app-page" inert={ended}>
      <a href="#main-content" className="skip-link">Skip to content</a>
      <SiteHeader />
      <main id="main-content" className={cls("shell", wide && "page-wide")} tabIndex={-1}><ShellNotices /><Outlet /></main>
      <ShellFooter />
      <BottomNav />
    </div>
    {ended && <SessionEndedCard />}
  </ToastProvider></ShellProvider></PollingPaused.Provider></QueryClientProvider>;
}

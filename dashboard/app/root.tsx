// SPDX-License-Identifier: MIT
// The document shell and the error boundary for every route, signed in or not.
import { useEffect, useRef, type ReactNode } from "react";
import { Links, Meta, Scripts, ScrollRestoration, data, isRouteErrorResponse, useLoaderData, useLocation, useRevalidator, useRouteError, useRouteLoaderData, type HeadersFunction, type LoaderFunctionArgs } from "react-router";
import { AppShell, PageCard } from "./components/app-shell";
import { PlainHeader } from "./components/site-header";
import { ApiError } from "./lib/operator-api";
import { SIGN_IN_REFUSALS, displayableRequestId, pageContext, signInRefusal } from "./lib/page-context";
import styles from "./styles.css?url";

// viewport-fit=cover lets env(safe-area-inset-*) clear the phone bar from the home indicator.
const VIEWPORT = "width=device-width, initial-scale=1, viewport-fit=cover";
/** The busy page retries on its own after this long (the limiter's `Retry-After: 5`). */
export const BUSY_RETRY_MS = 5000;

export const links = () => [{ rel: "stylesheet", href: styles }, { rel: "icon", href: "/favicon.svg", type: "image/svg+xml" }];
export const headers: HeadersFunction = ({ errorHeaders }) => {
  const headers = new Headers();
  const retry = errorHeaders?.get("Retry-After");
  if (retry) headers.set("Retry-After", retry);
  return headers;
};

/**
 * Server-side only, and nothing about the greenhouse: the CSP nonce, the BFF's build commit for the footer, the request
 * id and a sign-in refusal from #1141's `/auth/*` handlers, which the page answers with its status and card at the
 * same URL (SCR-09). The banners, the count, the role and the API's build load in the browser.
 */
export function loader({ request, context }: LoaderFunctionArgs) {
  const page = context.get(pageContext);
  const authError = signInRefusal(page.authError);
  const body = {
    nonce: request.headers.get("x-dashboard-csp-nonce") || "",
    sourceRevision: page.sourceRevision,
    requestId: displayableRequestId(page.requestId),
    authError,
  };
  return authError ? data(body, { status: SIGN_IN_REFUSALS[authError.reason].status }) : body;
}
/** Nothing the root loads changes during a visit, so a navigation never asks the server for it again. */
export const shouldRevalidate = () => false;

/** The page document. An error page names its own title and has no nonce: it renders without the route meta or scripts. */
function Document({ nonce, title, children }: { nonce?: string; title?: string; children: ReactNode }) {
  return <html lang="en"><head><meta charSet="utf-8" /><meta name="viewport" content={VIEWPORT} />{title ? <title>{title}</title> : <Meta />}<Links /></head><body>
    {children}
    {nonce !== undefined && <><ScrollRestoration nonce={nonce} /><Scripts nonce={nonce} /></>}
  </body></html>;
}

export default function Root() {
  const loaded = useLoaderData<typeof loader>();
  const nonce = useRef(loaded.nonce).current;
  return <Document nonce={nonce}><AppShell build={loaded.sourceRevision} authError={loaded.authError} /></Document>;
}

/** The request id an error page shows: the Problem's, else the page request's (`X-Request-Id`, from the BFF). */
function errorRequestId(error: unknown, pageRequestId: string | null | undefined): string | null {
  if (error instanceof ApiError) return displayableRequestId(error.problem.request_id) ?? null;
  return displayableRequestId(pageRequestId);
}

/**
 * SCR-09's error pages: the header without nav or tools and one card. Not found links to Now; the SSR limiter's busy
 * answer retries on its own after 5 s; anything else (a BFF 500, a render error) offers Retry and the request id. No
 * internal detail is shown.
 */
export function ErrorBoundary() {
  const error = useRouteError();
  const root = useRouteLoaderData<typeof loader>("root");
  const { pathname, search } = useLocation();
  const { revalidate, state } = useRevalidator();
  const busy = isRouteErrorResponse(error) && error.status === 503 && error.data === "busy";
  const notFound = isRouteErrorResponse(error) && error.status === 404;
  useEffect(() => {
    if (!busy || state !== "idle") return;
    const timer = setTimeout(() => { void revalidate(); }, BUSY_RETRY_MS);
    return () => clearTimeout(timer);
  }, [busy, state, revalidate]);
  const requestId = busy || notFound ? null : errorRequestId(error, root?.requestId);
  const title = busy ? "Busy, retrying in 5 s" : notFound ? "Page not found" : "Something went wrong";
  return <Document title={`${busy ? "Busy" : title} · Verdify dashboard`}>
    <PlainHeader />
    <main id="main-content" className="shell page-card-main error-page" tabIndex={-1}>
      <PageCard title={title}>
        {busy && <p>Too many pages are loading at once.</p>}
        {notFound && <p><a className="button-link" href="/">Return to Now</a></p>}
        {!busy && !notFound && <>
          <p><a className="button-link btn-primary" href={`${pathname}${search}`}>Retry</a></p>
          {requestId && <p className="small">Request id <span className="mono">{requestId}</span></p>}
        </>}
      </PageCard>
    </main>
  </Document>;
}

// SPDX-License-Identifier: MIT
import { useRef, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Links, Meta, Outlet, Scripts, ScrollRestoration, isRouteErrorResponse, useLoaderData, useRouteError, type HeadersFunction, type LoaderFunctionArgs } from "react-router";
import styles from "./styles.css?url";
export const links = () => [{ rel: "stylesheet", href: styles }];
export const headers: HeadersFunction = ({ errorHeaders }) => {
  const headers = new Headers();
  const retry = errorHeaders?.get("Retry-After");
  if (retry) headers.set("Retry-After", retry);
  return headers;
};
export function loader({ request }: LoaderFunctionArgs) {
  return { nonce: request.headers.get("x-dashboard-csp-nonce") || "" };
}
export default function Root() {
  const loaded = useLoaderData<typeof loader>();
  const nonce = useRef(loaded.nonce).current;
  // Each SSR render/request owns its cache. No process-global query state; greenhouse data loads in the browser.
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false } } }));
  return <html lang="en"><head><meta charSet="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" /><Meta /><Links /></head><body><a href="#main-content" className="skip-link">Skip to content</a>
    <main id="main-content" className="shell" tabIndex={-1}><QueryClientProvider client={client}><Outlet /></QueryClientProvider></main>
    <footer className="site-footer"><div className="shell"><span>Verdify dashboard</span><p className="small"><a href="/fonts/OFL-sans.txt">IBM Plex Sans license</a> · <a href="/fonts/OFL-mono.txt">IBM Plex Mono license</a></p></div></footer>
    <ScrollRestoration nonce={nonce} /><Scripts nonce={nonce} /></body></html>;
}
export function ErrorBoundary() {
  const error = useRouteError();
  const status = isRouteErrorResponse(error) ? error.status : 503;
  const busy = isRouteErrorResponse(error) && error.status === 503 && error.data === "busy";
  return <html lang="en"><head><meta charSet="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" /><title>{busy ? "Dashboard is busy" : status === 404 ? "Page not found" : "Dashboard unavailable"}</title><Links /></head><body><main className="shell error-page"><span className="eyebrow">Verdify dashboard</span><h1>{busy ? "Dashboard is busy" : status === 404 ? "Page not found" : "The dashboard is temporarily unavailable"}</h1><p>{busy ? "Too many pages are loading at once. Please retry in a few seconds." : "The requested page could not be loaded. No internal service details are included here."}</p><p><a className="button-link" href="/">Return to Now</a></p></main></body></html>;
}

// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { createRoutesStub, RouterContextProvider, UNSAFE_ErrorResponseImpl, type LoaderFunctionArgs } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { ToastProvider } from "./components/toast";
import { loader as authLoader, meta as authMeta } from "./routes/auth";
import { loader as notFoundLoader } from "./routes/not-found";
import { pageContext, type PageContext } from "./lib/page-context";
import { ApiError } from "./lib/operator-api";
import { BUSY_RETRY_MS, ErrorBoundary, headers, loader, shouldRevalidate } from "./root";
import Now, { meta } from "./routes/now";
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

const REVISION = "0123456789abcdef0123456789abcdef01234567";
const REQUEST_ID = "4f9d2c1e-7b8a-4c3d-9e2f-1a2b3c4d5e6f";
function context(values: Partial<PageContext> = {}) {
  const provider = new RouterContextProvider();
  provider.set(pageContext, { sourceRevision: REVISION, requestId: REQUEST_ID, authError: null, ...values });
  return provider;
}
function args(provider: RouterContextProvider, nonce = "n0nce"): LoaderFunctionArgs {
  return { request: new Request("https://dashboard.verdify.ai/auth/callback", { headers: { "x-dashboard-csp-nonce": nonce } }), params: {}, context: provider, url: new URL("https://dashboard.verdify.ai/auth/callback"), pattern: "/" } as unknown as LoaderFunctionArgs;
}
type Wrapped = { type?: string; data: unknown; init: { status?: number } | null };

async function boundaryFor(thrown: unknown, rootData?: unknown) {
  // The boundary renders <html>; React reports the jsdom container nesting, which is not under test.
  vi.spyOn(console, "error").mockImplementation(() => {});
  const Stub = createRoutesStub([{ id: "root", path: "/", loader: () => { throw thrown; }, Component: () => null, ErrorBoundary }]);
  render(<Stub initialEntries={["/"]} hydrationData={rootData === undefined ? undefined : { loaderData: { root: rootData } }} />);
  return screen.findByRole("heading", { level: 1 });
}

test("Now's server render is its loading state, with no greenhouse data and no request", () => {
  expect(meta()).toEqual([{ title: "Now · Verdify dashboard" }]);
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  const client = new QueryClient();
  // Now (#1129) loads its data in the browser, under the shell's query client and toasts.
  const Stub = createRoutesStub([{ path: "/", Component: () => <QueryClientProvider client={client}><ToastProvider><Now /></ToastProvider></QueryClientProvider> }]);
  const html = renderToString(<Stub initialEntries={["/"]} />);
  expect(html).toContain("Checking the greenhouse…");
  expect(html.match(/<h1\b/g)).toHaveLength(1);
  expect(html).not.toMatch(/All OK|64\.8|Nothing running|No open alerts|Nobody seen/);
  expect(fetch).not.toHaveBeenCalled();
});

test("the root loader gives the nonce, the BFF's build and request id, and nothing about the greenhouse", () => {
  expect(loader(args(context()))).toEqual({ nonce: "n0nce", sourceRevision: REVISION, requestId: REQUEST_ID, authError: null });
  // With no load context (a plain React Router server), the defaults hold.
  expect(loader(args(new RouterContextProvider(), ""))).toEqual({ nonce: "", sourceRevision: "unknown", requestId: null, authError: null });
  expect(shouldRevalidate()).toBe(false);
});

test.each([["failed", 400], ["unavailable", 503], ["no_access", 403]] as const)("a %s sign-in answers %i with its refusal at the same URL", (reason, status) => {
  const result = loader(args(context({ authError: { reason, requestId: REQUEST_ID } }))) as unknown as Wrapped;
  expect(result.init?.status).toBe(status);
  expect(result.data).toMatchObject({ authError: { reason, requestId: REQUEST_ID }, sourceRevision: REVISION });
  // The /auth/* route serves the card's URL; any other /auth path, and anything unknown, is not found.
  expect(authLoader(args(context({ authError: { reason, requestId: null } })))).toBeNull();
});

test("an unknown refusal or a stray value from res.locals is ignored, and /auth without one is not found", () => {
  for (const authError of [{ reason: "expired" }, "failed", { reason: "constructor" }, 42]) {
    expect((loader(args(context({ authError }))) as { authError: unknown }).authError).toBeNull();
  }
  expect((loader(args(context({ authError: { reason: "failed", requestId: "<script>" } }))) as unknown as Wrapped).data).toMatchObject({ authError: { reason: "failed", requestId: null } });
  for (const thrower of [() => authLoader(args(context())), () => notFoundLoader()]) {
    try { thrower(); expect.unreachable(); } catch (error) { expect((error as Wrapped).init?.status).toBe(404); }
  }
});

test("the /auth/* title names the refusal", () => {
  const titled = (authError: unknown) => authMeta({ matches: [{ id: "root", loaderData: { authError } }] } as unknown as Parameters<typeof authMeta>[0]);
  expect(titled({ reason: "no_access", requestId: null })).toEqual([{ title: "No dashboard access · Verdify dashboard" }]);
  expect(titled(null)).toEqual([{ title: "Sign in · Verdify dashboard" }]);
});

test.each([
  // single-fetch builds exactly this from server.mjs's non-React-Router 503 body.
  ["busy", new UNSAFE_ErrorResponseImpl(503, "Service Unavailable", "busy"), "Busy, retrying in 5 s", 2],
  ["anything else", new UNSAFE_ErrorResponseImpl(500, "Internal Server Error", null), "Something went wrong", 1],
] as const)("%s: the page reads %s, and only busy retries, after 5 s", async (_case, thrown, title, callsAfter) => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.spyOn(console, "error").mockImplementation(() => {});
  let calls = 0;
  const Stub = createRoutesStub([{
    id: "root", path: "/", Component: () => <h1>Now</h1>, ErrorBoundary,
    loader: () => { calls += 1; if (calls === 1) throw thrown; return null; },
  }]);
  render(<Stub initialEntries={["/"]} />);
  expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe(title);
  if (callsAfter === 2) expect(screen.getByText("Too many pages are loading at once.")).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(BUSY_RETRY_MS - 100); });
  expect(calls).toBe(1);
  // The retry revalidates the page's loaders; the page then renders as usual (React swaps the document back in a
  // browser; jsdom keeps the error page's singletons, so the count is what is checked here).
  await act(async () => { await vi.advanceTimersByTimeAsync(200); });
  expect(calls).toBe(callsAfter);
});

test("anything else reads Something went wrong with Retry and the request id; an unknown page is not found", async () => {
  expect((await boundaryFor(new UNSAFE_ErrorResponseImpl(503, "Service Unavailable", "Dashboard is temporarily unavailable."))).textContent).toBe("Something went wrong");
  expect(screen.getByRole("link", { name: "Retry" }).getAttribute("href")).toBe("/");
  cleanup();
  expect((await boundaryFor(new Error("render failed"))).textContent).toBe("Something went wrong");
  expect(screen.queryByText(/render failed/)).toBeNull();
  cleanup();
  // A refused API call names the Problem's request id.
  const problem = { type: "about:blank", title: "Dashboard API error", status: 500, code: "unavailable", retryable: true, min: null, max: null, current: null, retry_after_s: null, request_id: REQUEST_ID };
  await boundaryFor(new ApiError(500, problem));
  expect(screen.getByText(/Request id/).textContent).toBe(`Request id ${REQUEST_ID}`);
  cleanup();
  expect((await boundaryFor(new UNSAFE_ErrorResponseImpl(404, "Not Found", null))).textContent).toBe("Page not found");
  expect(screen.getByRole("link", { name: "Return to Now" }).getAttribute("href")).toBe("/");
  expect(screen.queryByRole("navigation")).toBeNull();
  expect(screen.getByRole("link", { name: "Verdify dashboard, home" })).toBeTruthy();
});

test("the busy Retry-After reaches the document response", () => {
  const args = (errorHeaders?: Headers) => ({ errorHeaders }) as Parameters<typeof headers>[0];
  expect(new Headers(headers(args(new Headers({ "Retry-After": "5" })))).get("Retry-After")).toBe("5");
  expect(new Headers(headers(args())).get("Retry-After")).toBeNull();
});

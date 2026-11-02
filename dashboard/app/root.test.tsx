// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, render, screen } from "@testing-library/react";
import { createRoutesStub, UNSAFE_ErrorResponseImpl } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { ErrorBoundary, headers } from "./root";
import routes from "./routes";
import Now, { meta } from "./routes/now";
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function boundaryFor(thrown: unknown) {
  // The boundary renders <html>; React reports the jsdom container nesting, which is not under test.
  vi.spyOn(console, "error").mockImplementation(() => {});
  const Stub = createRoutesStub([{ path: "/", loader: () => { throw thrown; }, Component: () => null, ErrorBoundary }]);
  render(<Stub initialEntries={["/"]} />);
  return screen.findByRole("heading", { level: 1 });
}

test("the only route is Now, and it renders its page head with no greenhouse data and no request", async () => {
  expect(routes).toEqual([{ index: true, file: "routes/now.tsx" }]);
  expect(meta()).toEqual([{ title: "Now · Verdify dashboard" }]);
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  const Stub = createRoutesStub([{ path: "/", Component: Now }]);
  const { container } = render(<Stub initialEntries={["/"]} />);
  expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("Now");
  expect(container.textContent).toBe("Verdify dashboardNowNot built yet.");
  expect(container.querySelectorAll(".page-head")).toHaveLength(1);
  expect(fetch).not.toHaveBeenCalled();
});

test("a client navigation's plain 503 busy .data response renders the busy state", async () => {
  // single-fetch builds exactly this from server.mjs's non-React-Router 503 body.
  expect((await boundaryFor(new UNSAFE_ErrorResponseImpl(503, "Service Unavailable", "busy"))).textContent).toBe("Dashboard is busy");
  expect(screen.getByText(/Please retry in a few seconds/)).toBeTruthy();
});

test("other failures stay the generic unavailable state, and an unknown page is not found", async () => {
  expect((await boundaryFor(new UNSAFE_ErrorResponseImpl(503, "Service Unavailable", "Dashboard is temporarily unavailable."))).textContent).toBe("The dashboard is temporarily unavailable");
  cleanup();
  expect((await boundaryFor(new Error("render failed"))).textContent).toBe("The dashboard is temporarily unavailable");
  expect(screen.queryByText(/render failed/)).toBeNull();
  cleanup();
  expect((await boundaryFor(new UNSAFE_ErrorResponseImpl(404, "Not Found", null))).textContent).toBe("Page not found");
  expect(screen.getByRole("link", { name: "Return to Now" }).getAttribute("href")).toBe("/");
});

test("the busy Retry-After reaches the document response", () => {
  const args = (errorHeaders?: Headers) => ({ errorHeaders }) as Parameters<typeof headers>[0];
  expect(new Headers(headers(args(new Headers({ "Retry-After": "5" })))).get("Retry-After")).toBe("5");
  expect(new Headers(headers(args())).get("Retry-After")).toBeNull();
});

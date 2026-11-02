// SPDX-License-Identifier: MIT
// Test helper for the write tests (not a test file): #1122's fixture API in process behind a fetch stub, which keeps
// the request log (GET /__requests: operationId, body and Idempotency-Key) and when each request was sent, plus the
// providers a write needs (query client, router, session and toasts). Timers are fake, pinned to the screens' clock.
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act, render, type RenderResult } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import { createFixtureHandler } from "../../e2e/fixture-api.mjs";
import { routeOf } from "../../e2e/fixtures/index.mjs";
import { ToastProvider } from "../components/toast";
import { DEFAULT_SESSION, SessionProvider, type Session } from "./session";

/** 10:42 America/Denver on 2026-11-02 (MST), the screens' clock. */
export const NOW = Date.parse("2026-11-02T17:42:00Z");
export const OPERATOR: Partial<Session> = { role: "operator", writesEnabled: true };
export type FixtureApi = ReturnType<typeof createFixtureHandler>;
export type Recorded = { method: string; path: string; operationId: string | null; idempotency_key: string | null; body: unknown };
export type FixtureOptions = { fixture?: string | string[]; fail?: Record<string, number>; confirm?: boolean };
type Sent = { at: number; operationId: string | null };

let sent: Sent[] = [];

/** Fake timers at NOW, a visible and focused tab, and the fixture API answering `fetch` at the request's own time. */
export function startFixtureApi(options: FixtureOptions = {}): FixtureApi {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  focusManager.setFocused(true);
  sent = [];
  const api = createFixtureHandler({ ...options, clock: () => Date.now() });
  vi.stubGlobal("fetch", vi.fn(async (input: string, init: RequestInit = {}) => {
    const method = (init.method ?? "GET").toUpperCase();
    sent.push({ at: Date.now(), operationId: routeOf(method, new URL(input, "http://dashboard.invalid").pathname) });
    const headers = Object.fromEntries(new Headers(init.headers).entries());
    const out = api.respond({ method, url: input, headers, body: typeof init.body === "string" ? init.body : undefined });
    const text = out.status === 204 || out.body === null || out.body === undefined ? null : typeof out.body === "string" ? out.body : JSON.stringify(out.body);
    return answer(out.status, text, out.headers);
  }));
  return api;
}

/**
 * The parts of a `Response` the client reads, with a body that resolves in microtasks: undici's `Response.json()`
 * needs real event-loop turns, which fake timers never give, so a poll's answer would render at a random later tick.
 */
function answer(status: number, text: string | null, headers: Record<string, string>): Response {
  const parsed = () => {
    if (text === null) throw new SyntaxError("No body");
    return JSON.parse(text) as unknown;
  };
  return { status, ok: status >= 200 && status < 300, type: "basic", headers: new Headers(headers), json: async () => parsed(), text: async () => text ?? "" } as unknown as Response;
}
export function stopFixtureApi() {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  focusManager.setFocused(undefined);
}

/** Every request since the fixture was selected (GET /__requests), or only `operationId`'s. */
export function requests(api: FixtureApi, operationId?: string): Recorded[] {
  const all = api.respond({ url: "/__requests" }).body.requests as Recorded[];
  return operationId === undefined ? all : all.filter((row) => row.operationId === operationId);
}
/** The writes among them: every method but GET. */
export const writes = (api: FixtureApi): Recorded[] => requests(api).filter((row) => row.method !== "GET");
/** When each request for `operationId` was sent, in ms after NOW. */
export const sentAt = (operationId: string): number[] => sent.filter((row) => row.operationId === operationId).map((row) => row.at - NOW);

/** Renders `ui` with a fresh query client, a router, a session (an operator with writes on unless `session` says otherwise) and toasts. */
export function renderWithApi(ui: ReactElement, session: Partial<Session> = OPERATOR): RenderResult & { client: QueryClient } {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false } } });
  const value = { ...DEFAULT_SESSION, ...session };
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>
    <MemoryRouter><SessionProvider {...value}><ToastProvider>{children}</ToastProvider></SessionProvider></MemoryRouter>
  </QueryClientProvider>;
  return Object.assign(render(ui, { wrapper }), { client });
}

/** Moves the fake clock and lets every request, render and timer it reaches settle. */
export async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

/** UUID v4 as `crypto.randomUUID()` makes it. */
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

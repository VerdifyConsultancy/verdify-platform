// SPDX-License-Identifier: MIT
// Test helper for the component tests (not a test file): a router and a session around the component under test.
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { DEFAULT_SESSION, SessionProvider, type Session } from "../lib/session";

/** Renders `ui` in a router and a session (the app's default, a viewer with writes off, unless `session` says otherwise). */
export function renderUi(ui: ReactElement, session: Partial<Session> = {}): RenderResult {
  const value = { ...DEFAULT_SESSION, ...session };
  const wrapper = ({ children }: { children: ReactNode }) => <MemoryRouter><SessionProvider {...value}>{children}</SessionProvider></MemoryRouter>;
  return render(ui, { wrapper });
}

/** The no-break space `formatValue` and `duration` put before a unit. */
export const NBSP = String.fromCharCode(0xa0);
/** An element's text with no-break spaces read as plain spaces. */
export const text = (node: Element | null | undefined): string => (node?.textContent ?? "").replaceAll(NBSP, " ");

/** Instants in America/Denver on 2026-11-02 (MST, UTC−7): `denver("06:02")` is 2026-11-02T13:02:00Z. */
export function denver(clock: string, seconds = 0): string {
  const [hours, minutes] = clock.split(":").map(Number);
  return new Date(Date.UTC(2026, 10, 2, hours + 7, minutes, seconds)).toISOString();
}

// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// SCR-02's daily scorecard (#1130) against the scorecard-30d fixture: the daily.* keys API-03 lists, read 8 at a time
// over the 30 local days with points=31, API-IRR-10 by zone over the same dates, a 60 s poll, and 30 rows.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { DAILY_KEYS } from "../../e2e/fixtures/world.mjs";
import { text } from "../components/test-render";
import { SessionProvider } from "../lib/session";
import { advance, requests, startFixtureApi, stopFixtureApi, type FixtureOptions } from "../lib/test-fixture-api";
import Scorecard, { chunks } from "./graphs-scorecard";

const Stub = createRoutesStub([{ path: "/graphs/scorecard", Component: Scorecard }, { path: "/graphs", Component: () => <h1>Graphs</h1> }]);
async function open(options: FixtureOptions = { fixture: "scorecard-30d" }) {
  const api = startFixtureApi(options);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><SessionProvider><Stub initialEntries={["/graphs/scorecard"]} /></SessionProvider></QueryClientProvider>);
  // A read that starts in a render another read's answer caused re-renders once a little time has passed.
  for (let pass = 0; pass < 6; pass++) await advance(10);
  return api;
}
const query = (path: string) => new URL(path, "http://dashboard.invalid").searchParams;
const { createObjectURL, revokeObjectURL } = URL;
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:scorecard");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => { cleanup(); stopFixtureApi(); URL.createObjectURL = createObjectURL; URL.revokeObjectURL = revokeObjectURL; });

test("chunks: at most 8 keys per request", () => {
  expect(chunks(DAILY_KEYS).map((list) => list.length)).toEqual([8, 2]);
  expect(chunks([])).toEqual([]);
});

test("the scorecard reads the daily keys 8 at a time over 30 local days, minutes by zone over the same dates, and shows 30 rows", async () => {
  const api = await open();
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Daily scorecard");
  const series = requests(api, "API-04").map((row) => query(row.path));
  expect(series.map((params) => params.getAll("metric"))).toEqual([DAILY_KEYS.slice(0, 8), DAILY_KEYS.slice(8)]);
  for (const params of series) {
    expect([params.get("from"), params.get("to"), params.get("points")]).toEqual(["2026-10-04T06:00:00.000Z", "2026-11-03T07:00:00.000Z", "31"]);
  }
  expect(requests(api, "API-IRR-10").map((row) => Object.fromEntries(query(row.path)))).toEqual([{ from: "2026-10-04", to: "2026-11-02", group_by: "zone" }]);
  const rows = within(screen.getByRole("region", { name: /Daily scorecard/ })).getAllByRole("row").filter((row) => row.closest("tbody"));
  expect(rows).toHaveLength(30);
  expect(text(rows[0].querySelector("th"))).toBe("Mon, Nov 2 · partial");
  expect(text(rows[1].querySelector("th"))).toBe("Sun, Nov 1 · 25 h");
  expect(text(rows[3])).toContain("— band grade kept 3 days");
  expect(screen.getByRole("link", { name: "CSV of this table" }).getAttribute("href")).toBe("blob:scorecard");
  expect(screen.getByRole("link", { name: "Graphs" }).getAttribute("href")).toBe("/graphs");
});

test("the table polls every 60 s, the same window each time", async () => {
  const api = await open();
  expect(requests(api, "API-04")).toHaveLength(2);
  await advance(60_000);
  await act(async () => {});
  expect(requests(api, "API-04")).toHaveLength(4);
  expect(requests(api, "API-IRR-10")).toHaveLength(2);
  expect(new Set(requests(api, "API-04").map((row) => query(row.path).get("from")))).toEqual(new Set(["2026-10-04T06:00:00.000Z"]));
  // The registry is read once.
  expect(requests(api, "API-03")).toHaveLength(1);
});

test("a failed minutes read reads couldn't load in its columns, with a Retry, while the rest of the table shows", async () => {
  await open({ fixture: "in-band", fail: { "API-IRR-10": 503 } });
  const table = within(screen.getByRole("region", { name: /Daily scorecard/ })).getByRole("table");
  const today = [...table.querySelectorAll("tbody tr")][0];
  expect([...today.querySelectorAll("td")].map(text).slice(4, 6)).toEqual(["24 gal", "— couldn't load"]);
  expect(screen.getByText(/Some columns couldn't load/)).toBeTruthy();
});

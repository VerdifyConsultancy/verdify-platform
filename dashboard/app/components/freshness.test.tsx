// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Ago, AgeLabel, FreshnessDot, ServerClockProvider, serverClockSkew } from "./freshness";
import { denver, renderUi, text } from "./test-render";

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date(denver("06:16"))); });
afterEach(() => { cleanup(); vi.useRealTimers(); });
const dot = (container: HTMLElement) => container.querySelector(".dot")!;

test("the dot is ok, amber when stale, fault when not reporting, and never ok while the API is down", () => {
  expect(dot(renderUi(<FreshnessDot stale={false} />).container).getAttribute("data-fresh")).toBe("ok");
  cleanup();
  expect(dot(renderUi(<FreshnessDot stale />).container).className).toBe("dot fresh-stale");
  cleanup();
  expect(dot(renderUi(<FreshnessDot stale notReporting />).container).className).toBe("dot fresh-off");
  cleanup();
  const down = dot(renderUi(<FreshnessDot stale={false} />, { apiDown: true }).container);
  expect(down.getAttribute("data-fresh")).toBe("off");
  expect(down.getAttribute("aria-hidden")).toBe("true");
});

test("ages read \"Updated 20 s ago\" and, when the API marks the value stale, \"Last data 14 min ago (06:02)\"", () => {
  expect(text(renderUi(<AgeLabel observedAt={denver("06:15", 40)} stale={false} />).container)).toBe("Updated 20 s ago");
  cleanup();
  const { container } = renderUi(<AgeLabel observedAt={denver("06:02")} stale />);
  expect(text(container)).toBe("Last data 14 min ago (06:02)");
  expect(container.querySelector("time")?.getAttribute("dateTime")).toBe(denver("06:02"));
  cleanup();
  expect(text(renderUi(<AgeLabel observedAt={null} stale />).container)).toBe("No data yet");
  cleanup();
  // A day or more old, the time carries its date (Oct 30 is still daylight time, UTC−6).
  expect(text(renderUi(<AgeLabel observedAt="2026-10-30T12:02:00Z" stale />).container)).toBe("Last data 3 d ago (Oct 30, 06:02)");
});

test("staleness is the API's call: an old value the API calls fresh is not turned stale by its age", () => {
  expect(text(renderUi(<AgeLabel observedAt={denver("05:16")} stale={false} />).container)).toBe("Updated 1 h ago");
});

test("ages tick silently each second, with no live region", async () => {
  const { container } = renderUi(<><FreshnessDot stale={false} /><AgeLabel observedAt={denver("06:15", 40)} stale={false} /></>);
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(text(container)).toBe("Updated 25 s ago");
  expect(container.querySelector("[aria-live],[role=status],[role=alert]")).toBeNull();
});

test("a device clock running ahead still ages readings on the server's clock (weather's CurrentConditions rule)", () => {
  // The device reads 06:26:00 while the server's response said 06:16:00: a 10 min lead.
  vi.setSystemTime(new Date(denver("06:26")));
  const skew = serverClockSkew(denver("06:16"), Date.now());
  expect(skew).toBe(600_000);
  const { container } = renderUi(<ServerClockProvider skewMs={skew}><AgeLabel observedAt={denver("06:15", 40)} stale={false} /></ServerClockProvider>);
  expect(text(container)).toBe("Updated 20 s ago");
  // Under 2 s is latency, not skew; an unreadable server time is no skew.
  expect(serverClockSkew(denver("06:25", 59), Date.parse(denver("06:26")) + 500)).toBe(0);
  expect(serverClockSkew("garbage", Date.now())).toBe(0);
  expect(serverClockSkew(null, Date.now())).toBe(0);
});

test("Ago: the one age, ticking silently on the server's clock, in a <time> with its full local form; unparseable reads as unavailable", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-02T17:42:00Z"));
  const { container } = render(<Ago at="2026-11-02T17:28:00Z" />);
  const time = container.querySelector("time")!;
  expect(text(time)).toBe("14 min ago");
  expect(time.getAttribute("dateTime")).toBe("2026-11-02T17:28:00Z");
  expect(time.getAttribute("title")).toMatch(/^11\/02\/2026, 10:28:00 GMT-07:00$/);
  cleanup();
  expect(render(<Ago at="not a time" />).container.textContent).toBe("time unavailable");
  cleanup();
  expect(render(<Ago at={null} unavailable="—" />).container.textContent).toBe("—");
  cleanup();
  expect(render(<Ago at="2026-11-02T17:41:00Z" words={(seconds) => `${seconds} s back`} />).container.textContent).toBe("60 s back");
  vi.useRealTimers();
});

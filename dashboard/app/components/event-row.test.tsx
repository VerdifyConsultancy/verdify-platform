// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { Badge } from "./badge";
import { EventRow, attribution } from "./event-row";
import { denver, renderUi, text } from "./test-render";

afterEach(cleanup);

test("attribution names who wrote in text, with AI for iris and agents and never for system", () => {
  expect(attribution({ source: "dashboard", name: "Jason" })).toEqual({ text: "Jason · dashboard", ai: false });
  expect(attribution({ source: "iris", name: "iris" })).toEqual({ text: "iris", ai: true });
  expect(attribution({ source: "agent:steward", name: "steward" })).toEqual({ text: "steward · agent", ai: true });
  expect(attribution({ source: "agent:admin", name: "admin" })).toEqual({ text: "admin · agent", ai: true });
  expect(attribution({ source: "system", name: "forecast-engine" })).toEqual({ text: "forecast-engine · system", ai: false });
  // A source outside the pattern shows raw and never claims AI.
  expect(attribution({ source: "agent:Bad Name", name: "x" })).toEqual({ text: "x · agent:Bad Name", ai: false });
  expect(attribution({ source: "slack", name: "U123" })).toEqual({ text: "U123 · slack", ai: false });
});

test("a row: mono time, bold title with badges, muted attribution and detail, then the reason", () => {
  const { container } = renderUi(<ol className="events"><EventRow at={denver("07:41")} title="VPD Mister Engage 1.60 → 1.55 kPa" badges={<Badge kind="confirmed" />}
    actor={{ source: "dashboard", name: "Jason" }} detail="manual" reason="“Vandas drying”" /></ol>);
  const item = container.querySelector("li.event")!;
  const time = item.querySelector(".event-when time")!;
  expect(time.textContent).toBe("07:41");
  expect(time.getAttribute("dateTime")).toBe(denver("07:41"));
  expect(time.getAttribute("title")).toContain("GMT-07:00");
  expect(text(screen.getByRole("heading", { level: 3 }))).toBe("VPD Mister Engage 1.60 → 1.55 kPa Confirmed");
  const lines = [...item.querySelectorAll(".event-body p")].map(text);
  expect(lines).toEqual(["Jason · dashboard · manual", "“Vandas drying”"]);
  expect(item.querySelector("[data-badge=ai]")).toBeNull();
});

test("Iris and agent rows carry AI beside the name; a forecast-engine row never does", () => {
  for (const [actor, ai] of [[{ source: "iris", name: "iris" }, true], [{ source: "agent:steward", name: "steward" }, true], [{ source: "system", name: "forecast-engine" }, false]] as const) {
    const { container } = renderUi(<ol className="events"><EventRow at={denver("06:30")} title="Mister pulse gap 45 → 60 s" actor={actor} /></ol>);
    expect(container.querySelector(".event-by [data-badge=ai]") !== null, actor.source).toBe(ai);
    cleanup();
  }
});

test("a row from another day shows its date", () => {
  const { container } = renderUi(<ol className="events"><EventRow at="2026-10-31T13:41:00Z" showDate title="Plan iris-20261031-0741" /></ol>);
  expect(container.querySelector("time")?.textContent).toBe("Oct 31, 07:41");
});

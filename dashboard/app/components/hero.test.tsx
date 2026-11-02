// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Badge } from "./badge";
import { ServerClockProvider, serverClockSkew } from "./freshness";
import { Hero, type HeroProps, type HeroTile } from "./hero";
import { denver, renderUi, text } from "./test-render";

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date(denver("06:16"))); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

// The eight tiles of screens SCR-01; Mode, Iris, Water and Alerts stay in the band on phones.
const TILES: HeroTile[] = [
  { name: "vpd", label: "VPD", value: 0.62, unit: "kPa", verdict: "in band" },
  { name: "rh", label: "RH", value: 71, unit: "%" },
  { name: "outdoor", label: "Outdoor", value: 41.2, unit: "°F", observedAt: denver("06:14") },
  { name: "mode", label: "Mode", text: "In band, holding", phone: true },
  { name: "iris", label: "Iris", text: "OK · Sunrise", phone: true },
  { name: "water", label: "Water", text: "Center mist", phone: true, badge: <Badge kind="running" /> },
  { name: "alerts", label: "Alerts", text: "1 warning", phone: true },
  { name: "data", label: "Data", text: "Fresh", id: "data" },
];
const hero = (props: Partial<HeroProps> = {}) => <Hero eyebrow="Greenhouse · Longmont · America/Denver" title="All OK" value={64.8} unit="°F"
  verdict="in band" sub="band in force 62.0–70.0 °F · DERIVED" observedAt={denver("06:15", 40)} tiles={TILES} {...props} />;

test("the ink band: eyebrow, the answer as the title, the hero value, sub-lines and freshness", () => {
  const { container } = renderUi(hero());
  expect(container.querySelector("section.hero")).not.toBeNull();
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("All OK");
  expect(text(container.querySelector(".eyebrow"))).toBe("Greenhouse · Longmont · America/Denver");
  expect(text(container.querySelector(".hero-temp"))).toBe("64.8 °F");
  const meta = [...container.querySelectorAll(".hero-meta")].map(text);
  expect(meta).toEqual(["in band", "band in force 62.0–70.0 °F · DERIVED", "Updated 20 s ago"]);
  expect(container.querySelector(".hero-meta .dot")?.getAttribute("data-fresh")).toBe("ok");
  expect(container.innerHTML).not.toMatch(/\sstyle=/);
});

test("eight tiles 4 × 2; on phones four stay and the rest move into \"All readings\", copied without their ids", () => {
  const { container } = renderUi(hero({ readings: <p>Every reading with its time</p> }));
  const band = container.querySelector(".hero > dl.stats")!;
  expect([...band.querySelectorAll(":scope > div")].map((tile) => tile.getAttribute("data-tile"))).toEqual(TILES.map((tile) => tile.name));
  expect([...band.querySelectorAll(":scope > .stat-more")].map((tile) => tile.getAttribute("data-tile"))).toEqual(["vpd", "rh", "outdoor", "data"]);
  const details = container.querySelector(".hero details")!;
  expect(text(details.querySelector("summary"))).toBe("All readings");
  expect([...details.querySelectorAll("dl.stats-phone > div")].map((tile) => tile.getAttribute("data-tile"))).toEqual(["vpd", "rh", "outdoor", "data"]);
  expect(container.querySelectorAll("#data")).toHaveLength(1);
  expect(text(details)).toContain("Every reading with its time");
  // With nothing else to show, the disclosure exists for phones only.
  cleanup();
  expect(renderUi(hero()).container.querySelector(".hero details")?.className).toBe("hero-more-phone");
  cleanup();
  // With no tile marked, the first four stay.
  const plain = renderUi(hero({ tiles: TILES.map((tile) => ({ ...tile, phone: undefined })) })).container;
  expect([...plain.querySelectorAll(".hero > dl.stats > .stat-more")].map((tile) => tile.getAttribute("data-tile"))).toEqual(["iris", "water", "alerts", "data"]);
});

test("a null hero value is \"—\" with its reason", () => {
  const { container } = renderUi(hero({ value: null, reason: "Indoor probe not reporting" }));
  expect(text(container.querySelector(".hero-temp"))).toBe("—");
  expect(text(container)).toContain("Indoor probe not reporting");
  expect(text(container.querySelector(".hero-temp"))).not.toContain("0");
});

test("stale or not reporting: STALE with the last data time, muted values and no verdict", () => {
  let { container } = renderUi(hero({ observedAt: denver("06:02"), stale: true }));
  expect(text(container.querySelector(".hero-meta:last-of-type"))).toBe("Last data 14 min ago (06:02)Stale");
  expect(container.querySelector(".hero-temp")?.className).toBe("hero-temp value-muted");
  expect(text(container)).not.toContain("in band");
  expect(container.querySelector(".hero-meta .dot")?.getAttribute("data-fresh")).toBe("stale");
  cleanup();
  ({ container } = renderUi(hero({ observedAt: denver("06:02"), notReporting: true })));
  expect(container.querySelector(".hero-meta .dot")?.getAttribute("data-fresh")).toBe("off");
  expect(text(container)).not.toContain("in band");
});

test("while the dashboard API is down nothing in the hero is green and no verdict shows", () => {
  const { container } = renderUi(hero(), { apiDown: true });
  expect(container.querySelector(".hero-meta .dot")?.getAttribute("data-fresh")).toBe("off");
  expect(container.querySelector(".tag-ok,.status-complete,.fresh-ok")).toBeNull();
  expect(text(container)).not.toContain("in band");
});

test("a device clock running ahead still reads the hero's age on the server's clock", () => {
  vi.setSystemTime(new Date(denver("06:26")));
  const skew = serverClockSkew(denver("06:16"), Date.now());
  const { container } = renderUi(<ServerClockProvider skewMs={skew}>{hero()}</ServerClockProvider>);
  expect(text(container.querySelector(".hero-meta:last-of-type"))).toBe("Updated 20 s ago");
});

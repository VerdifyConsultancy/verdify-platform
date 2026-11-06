// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Now's quick actions (redesign §5.1, D-UX-05) against the fixture API in process: Stop all water first, then Fans 15 min,
// Humidify 5 min and Vent bypass 10 min, each one API-IRR-12 with no dialog; before the irrigation firmware Stop all water
// and the words that the bursts arrive with it; no row at all for a viewer; Stop stays live with changes switched off.
// Then the lights (#1342): Main light and Grow light with their state in words and Equipment's own buttons (API-20).
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { panelRead, useApiRead } from "../../lib/queries";
import { UUID, advance, renderWithApi, startFixtureApi, stopFixtureApi, writes } from "../../lib/test-fixture-api";
import { text } from "../test-render";
import { QuickActions } from "./quick-actions";

afterEach(() => { cleanup(); stopFixtureApi(); });

function Harness({ mode = null, stop = vi.fn() }: { mode?: string | null; stop?: () => void }) {
  const irrigation = panelRead(useApiRead("API-IRR-01", {}, { intervalMs: 15_000 }));
  return <QuickActions stop={{ stop, sending: false, confirming: false }} irrigation={irrigation} mode={mode} />;
}
/** The water and burst buttons: the lights' and the terms' are read on their own. */
const names = () => [...document.querySelectorAll(".now-actions button:not(.term)")].filter((button) => !button.closest(".now-lights")).map((button) => text(button));
const lightButtons = () => [...document.querySelectorAll(".now-lights button:not(.term)")].map((button) => text(button));

test("an operator: Stop all water first, then the three bursts, each a name over its length", async () => {
  startFixtureApi({ fixture: "in-band" });
  renderWithApi(<Harness />);
  await advance(0);
  expect(screen.getByRole("region", { name: "Quick actions" })).toBeTruthy();
  expect(names()).toEqual(["Stop all water", "Fans 15 min", "Humidify 5 min", "Vent bypass 10 min"]);
  expect(screen.getByRole("button", { name: "Stop all water" }).className).toContain("btn-stop");
  // The shared BurstButton, as Equipment's: the name bold, the length muted (#1308 visual QA 39).
  expect(document.querySelector(".now-burst-button.burst-button .burst-name")?.textContent).toBe("Fans");
  expect(document.querySelector(".now-burst-button .burst-length")?.textContent).toBe("15 min");
});

test("a burst is one API-IRR-12 with its kind and minutes and no dialog; Stop all water calls the route's Stop", async () => {
  const api = startFixtureApi({ fixture: "in-band" });
  const stop = vi.fn();
  renderWithApi(<Harness stop={stop} />);
  await advance(0);
  fireEvent.click(screen.getByRole("button", { name: "Fans 15 min" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-12", body: { kind: "fans", minutes: 15 } })]);
  expect(writes(api)[0].idempotency_key).toMatch(UUID);
  expect(document.querySelector("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Stop all water" }));
  expect(stop).toHaveBeenCalledOnce();
});

test("before the irrigation firmware: Stop all water and the words that the bursts arrive with it; during emergency cooling the bursts say they are ignored", async () => {
  startFixtureApi({ fixture: "before-engine" });
  renderWithApi(<Harness />);
  await advance(0);
  expect(names()).toEqual(["Stop all water"]);
  expect(text(document.querySelector("[data-arrives=bursts]"))).toBe("Bursts arrive with the irrigation firmware");
  cleanup();
  stopFixtureApi();
  startFixtureApi({ fixture: "safety-cool" });
  renderWithApi(<Harness mode="SAFETY_COOL" />);
  await advance(0);
  expect(text(document.querySelector("[data-bursts=ignored]"))).toBe("Bursts are ignored during emergency cooling");
});

test("with changes switched off the bursts are disabled and Stop all water stays live; a viewer gets no row; an unknown engine keeps the row's height", async () => {
  startFixtureApi({ fixture: ["in-band", "writes-off"] });
  renderWithApi(<Harness />, { role: "operator", writesEnabled: false });
  await advance(0);
  expect(screen.getByRole("button", { name: "Stop all water" }).hasAttribute("disabled")).toBe(false);
  for (const name of ["Fans 15 min", "Humidify 5 min", "Vent bypass 10 min"]) expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(true);
  cleanup();
  stopFixtureApi();
  startFixtureApi({ fixture: ["in-band", "viewer-session"] });
  renderWithApi(<Harness />, { role: "viewer", writesEnabled: false });
  await advance(0);
  expect(document.querySelector(".now-actions")).toBeNull();
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(0);
  cleanup();
  stopFixtureApi();
  startFixtureApi({ fixture: "in-band", fail: { "API-IRR-01": 503 } });
  renderWithApi(<Harness />);
  expect(document.querySelector(".now-bursts-skeleton")).not.toBeNull();
  await advance(0);
  expect(text(document.querySelector(".now-actions-note"))).toBe("Can't tell whether the controller has the irrigation firmware yet. Retry");
  expect(names()).toEqual(["Stop all water", "Retry"]);
});

test("the lights: Main light and Grow light after the bursts, each its state in words and Equipment's buttons; a Turn on is one API-20 (#1342)", async () => {
  const api = startFixtureApi({ fixture: "in-band" });
  renderWithApi(<Harness />);
  await advance(0);
  const group = screen.getByRole("group", { name: "Lights" });
  expect([...group.querySelectorAll(".now-light-head")].map(text)).toEqual(["Main light off · Automatic", "Grow light off · Automatic"]);
  expect([...group.querySelectorAll(".now-light-head button.term")].map(text)).toEqual(["Main light", "Grow light"]);
  expect(lightButtons()).toEqual(["Turn on 15 min Main light", "Turn on 15 min Grow light"]);
  expect(group.querySelector("a[href='/equipment#lights']")).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Turn on 15 min Main light" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-20", path: "/api/v1/equipment/lights/main/on", body: null })]);
  expect(document.querySelector("dialog")).toBeNull();
});

test("the lights do not wait for the irrigation firmware; with changes off they are disabled; a viewer has none", async () => {
  startFixtureApi({ fixture: "before-engine" });
  renderWithApi(<Harness />);
  await advance(0);
  expect(lightButtons()).toEqual(["Turn on 15 min Main light", "Turn on 15 min Grow light"]);
  cleanup();
  stopFixtureApi();
  startFixtureApi({ fixture: ["in-band", "writes-off"] });
  renderWithApi(<Harness />, { role: "operator", writesEnabled: false });
  await advance(0);
  for (const button of document.querySelectorAll(".now-lights button:not(.term)")) expect(button.hasAttribute("disabled")).toBe(true);
  cleanup();
  stopFixtureApi();
  startFixtureApi({ fixture: ["in-band", "viewer-session"] });
  renderWithApi(<Harness />, { role: "viewer", writesEnabled: false });
  await advance(0);
  expect(document.querySelector(".now-lights")).toBeNull();
});

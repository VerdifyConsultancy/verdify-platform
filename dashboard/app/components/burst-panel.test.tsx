// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The bursts and Restart controller (#1136) in the shell against #1122's fixture API in process: each button's one
// API-IRR-12 with no dialog, what runs from API-02 `overrides` alone, Restart until the uptime drops, the words before the
// irrigation firmware, viewers and changes off. The clock is 10:42 America/Denver on 2026-11-02.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { NOW, advance, requests, sentAt, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import { AppShell } from "./app-shell";
import { BurstPanel, burstWords, burstsIgnoredText } from "./burst-panel";
import { text } from "./test-render";

afterEach(() => { cleanup(); stopFixtureApi(); });

async function open(options: FixtureOptions = {}) {
  const api = startFixtureApi(options);
  const Stub = createRoutesStub([{ path: "/", Component: () => <AppShell build="fedcba9876543210fedcba9876543210fedcba98" authError={null} />, children: [{ path: "equipment", Component: BurstPanel }] }]);
  render(<Stub initialEntries={["/equipment"]} />);
  await advance(0);
  return api;
}
/** Moves the clock; a read's answer reaches the page on the tick after it (TanStack batches its notifications on a timer). */
const readBack = async (ms: number) => { await advance(ms); await advance(1); };
const press = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));
const button = (name: string | RegExp) => screen.queryByRole("button", { name });
const bursts = () => [...document.querySelectorAll(".burst-panel [data-chip=burst]")].map((node) => text(node));
const running = () => text(document.querySelector("[data-bursts]"));
const iso = (ms: number) => new Date(ms).toISOString();
const burst = (fields: Partial<Parameters<typeof burstWords>[0]>) => ({ kind: "fans", since: iso(NOW - 60_000), ends_at: null, held_reason: null, ...fields });

test("what a running burst reads: the time left, none for a push-button burst, and why it is held off, in words", () => {
  const words = (fields: Partial<Parameters<typeof burstWords>[0]>) => burstWords(burst(fields), NOW).replaceAll(String.fromCharCode(0xa0), " ");
  expect(words({ ends_at: iso(NOW + 12 * 60_000) })).toBe("Fans burst · 12 min left");
  expect(words({})).toBe("Fans burst");
  expect(words({ ends_at: iso(NOW - 60_000) })).toBe("Fans burst");
  expect(words({ kind: "vent_bypass", ends_at: iso(NOW + 600_000) })).toBe("Vent bypass · 10 min left");
  expect(words({ kind: "humidify", ends_at: iso(NOW + 300_000), held_reason: "occupancy" })).toBe("Humidify held off: someone inside");
  // An unknown code shows raw, its case included.
  expect(words({ kind: "humidify", held_reason: "new_reason" })).toBe("Humidify held off: new_reason");
  expect(words({ kind: "humidify", held_reason: "RH_CEILING" })).toBe("Humidify held off: RH_CEILING");
  expect(burstsIgnoredText("SAFETY_COOL")).toBe("Bursts are ignored during emergency cooling");
  expect(burstsIgnoredText("SAFETY_HEAT")).toBe("Bursts are ignored during emergency heating");
  expect(burstsIgnoredText("SENSOR_FAULT")).toBe("Bursts are ignored during a sensor fault");
  expect(burstsIgnoredText("IDLE")).toBeNull();
  expect(burstsIgnoredText(null)).toBeNull();
});

test("Fans 15 min: one {kind, minutes} with no dialog, Sending… until the 202, and the chip reads once fan_burst_active turns on, never from the command", async () => {
  const api = await open({ fixture: "burst-readback-late" });
  expect(running()).toBe("Running: none");
  press("Fans 15 min");
  expect(button("Sending…")).toBeTruthy();
  expect(document.querySelector("dialog")).toBeNull();
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-12", path: "/api/v1/equipment/override", body: { kind: "fans", minutes: 15 } })]);
  expect(text(document.querySelector(".burst-actions .state-note"))).toBe("Waiting for the controller");
  // The readback is API-02 every 5 s; the flag turns on 10 s after the command.
  await readBack(5000);
  expect(sentAt("API-02")).toContain(5000);
  expect(running()).toBe("Running: none");
  await readBack(4999);
  expect(bursts()).toEqual(["Fans burst · 15 min left"]);
  expect(document.querySelector(".burst-actions .state-note")).toBeNull();
  expect(writes(api)).toHaveLength(1);
});

test.each([
  // Read 5 s after the ON edge.
  ["Humidify 5 min", { kind: "humidify", minutes: 5 }, "Humidify burst · 4 min left"],
  ["Vent bypass 10 min", { kind: "vent_bypass", minutes: 10 }, "Vent bypass · 9 min left"],
])("%s sends its {kind, minutes} and its readback shows", async (name, body, chip) => {
  const api = await open();
  press(name);
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-12", body })]);
  await readBack(5000);
  expect(bursts()).toEqual([chip]);
});

test("a Humidify burst while someone is inside reads held off, in words", async () => {
  await open({ fixture: "occupied" });
  press("Humidify 5 min");
  await advance(0);
  await readBack(5000);
  expect(bursts()).toEqual(["Humidify held off: someone inside"]);
});

test("Clear sends one {kind: clear} and its readback reads none", async () => {
  const api = await open({ fixture: "push-button-burst" });
  expect(bursts()).toEqual(["Fans burst"]);
  press("Clear bursts");
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-12", body: { kind: "clear" } })]);
  await readBack(5000);
  expect(running()).toBe("Running: none");
});

test("each burst reads as on Now, its name bold and its length muted; Clear bursts shows only while a burst runs, or while what runs is unknown (#1308 visual QA 39)", async () => {
  await open();
  const buttons = [...document.querySelectorAll<HTMLElement>(".burst-actions .burst-button")];
  expect(buttons.map((node) => [text(node.querySelector(".burst-name")), text(node.querySelector(".burst-length"))])).toEqual([["Fans", "15 min"], ["Humidify", "5 min"], ["Vent bypass", "10 min"]]);
  expect(buttons.every((node) => node.className.includes("btn-wide"))).toBe(true);
  // Nothing runs: nothing to clear.
  expect(running()).toBe("Running: none");
  expect(button("Clear bursts")).toBeNull();
  cleanup(); stopFixtureApi();
  await open({ fixture: "push-button-burst" });
  expect(button("Clear bursts")?.textContent).toBe("Clear bursts");
  cleanup(); stopFixtureApi();
  // The controller not reporting: what runs is unknown, so Clear stays.
  await open({ fixture: "controller-not-reporting" });
  expect(running()).toBe("Running: unknown");
  expect(button("Clear bursts")).not.toBeNull();
});

test("during emergency cooling the panel says bursts are ignored", async () => {
  await open({ fixture: "safety-cool" });
  expect(text(document.querySelector("[data-bursts=ignored]"))).toBe("Bursts are ignored during emergency cooling");
});

test("Restart controller sits under More actions: one {kind: restart} with no dialog, Restart sent until the uptime drops", async () => {
  const api = await open({ fixture: "restart-uptime-reset" });
  expect(text(document.querySelector(".burst-more summary"))).toBe("More actions");
  expect(document.querySelector(".burst-more [data-write-control]")?.textContent).toBe("Restart controller");
  fireEvent.click(document.querySelector(".burst-more [data-write-control]")!);
  expect(document.querySelector("dialog")).toBeNull();
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-12", path: "/api/v1/equipment/override", body: { kind: "restart" } })]);
  const restart = () => document.querySelector<HTMLButtonElement>(".burst-more [data-write-control]")!;
  expect(restart().textContent).toBe("Restart sent");
  expect(restart().disabled).toBe(true);
  await readBack(5000);
  expect(restart().textContent).toBe("Restart sent");
  // The fixture resets `uptime_s` on the third API-02 read after the command.
  await readBack(10_000);
  expect(restart().textContent).toBe("Restart controller");
  expect(restart().disabled).toBe(false);
  expect(text(document.querySelector(".burst-more .state-note"))).toBe("The controller restarted");
  expect(writes(api)).toHaveLength(1);
});

test("a restart with no uptime drop within 3 min says so, and the window is read every 5 s", async () => {
  await open();
  // A controller that never comes back: the fixture answers with the uptime as it was.
  const fetcher = globalThis.fetch as (input: string, init?: RequestInit) => Promise<Response>;
  const held = await (await fetcher("/api/v1/now")).json() as { controller: unknown };
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const answer = await fetcher(input, init);
    if (input !== "/api/v1/now") return answer;
    const body = { ...(await answer.json() as object), controller: held.controller };
    return { ...answer, headers: answer.headers, json: async () => body } as unknown as Response;
  }));
  fireEvent.click(document.querySelector(".burst-more [data-write-control]")!);
  await advance(0);
  await readBack(175_000);
  expect(document.querySelector(".burst-more [data-write-control]")?.textContent).toBe("Restart sent");
  await readBack(5000);
  expect(document.querySelector(".burst-more [data-write-control]")?.textContent).toBe("Restart controller");
  expect(text(document.querySelector(".burst-more .state-note"))).toBe("The controller has not restarted");
});

/** API-02 answers with `uptime_s` from here on; the function it returns moves it. */
function pinUptime(first: number) {
  let uptime = first;
  const fetcher = globalThis.fetch as (input: string, init?: RequestInit) => Promise<Response>;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const answer = await fetcher(input, init);
    if (input !== "/api/v1/now") return answer;
    const body = await answer.json() as { controller: object };
    body.controller = { ...body.controller, uptime_s: uptime };
    return { ...answer, headers: answer.headers, json: async () => body } as unknown as Response;
  }));
  return (next: number) => { uptime = next; };
}
const restartReads = () => document.querySelector(".burst-more [data-write-control]")?.textContent;

test("Restart reads a drop from the last read: a controller up only 10 s at the press, back at 12 s after climbing to 15", async () => {
  await open();
  const setUptime = pinUptime(10);
  await readBack(15_000);
  fireEvent.click(document.querySelector(".burst-more [data-write-control]")!);
  await advance(0);
  setUptime(15);
  await readBack(5000);
  expect(restartReads()).toBe("Restart sent");
  setUptime(12);
  await readBack(5000);
  expect(restartReads()).toBe("Restart controller");
  expect(text(document.querySelector(".burst-more .state-note"))).toBe("The controller restarted");
});

test("a drop after the 3 min still reads restarted, on the shell's own poll", async () => {
  await open();
  const setUptime = pinUptime(5000);
  await readBack(15_000);
  fireEvent.click(document.querySelector(".burst-more [data-write-control]")!);
  await advance(0);
  await readBack(180_000);
  expect(text(document.querySelector(".burst-more .state-note"))).toBe("The controller has not restarted");
  setUptime(20);
  await readBack(15_000);
  expect(text(document.querySelector(".burst-more .state-note"))).toBe("The controller restarted");
});

test("a burst that reads back after its 30 s drops Not started, on the shell's own poll", async () => {
  await open();
  const fetcher = globalThis.fetch as (input: string, init?: RequestInit) => Promise<Response>;
  let hidden = true;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const answer = await fetcher(input, init);
    if (input !== "/api/v1/now" || !hidden) return answer;
    const body = { ...(await answer.json() as object), overrides: [] };
    return { ...answer, headers: answer.headers, json: async () => body } as unknown as Response;
  }));
  press("Fans 15 min");
  await advance(0);
  await readBack(30_000);
  expect(text(document.querySelector(".burst-actions .state-note"))).toBe("Not started");
  hidden = false;
  await readBack(15_000);
  expect(bursts()).toHaveLength(1);
  expect(document.querySelector(".burst-actions .state-note")).toBeNull();
});

test("before the irrigation firmware: the bursts and Restart read arrives with the irrigation firmware, with no button, and what runs still shows", async () => {
  await open({ fixture: ["push-button-burst", "before-engine"] });
  expect(text(document.querySelector("[data-arrives=bursts]"))).toBe("Bursts arrive with the irrigation firmware (manual_override)");
  expect(text(document.querySelector("[data-arrives=restart]"))).toBe("Restart arrives with the irrigation firmware");
  expect(document.querySelectorAll(".burst-panel [data-write-control]")).toHaveLength(0);
  expect(bursts()).toEqual(["Fans burst"]);
});

test("a viewer reads what runs, with no control and no words about controls", async () => {
  await open({ fixture: ["push-button-burst", "viewer-session"] });
  expect(bursts()).toEqual(["Fans burst"]);
  expect(document.querySelectorAll(".burst-panel button")).toHaveLength(0);
  expect(document.querySelector(".burst-more")).toBeNull();
  cleanup(); stopFixtureApi();
  await open({ fixture: ["before-engine", "viewer-session"] });
  expect(document.querySelector("[data-arrives]")).toBeNull();
  expect(running()).toBe("Running: none");
});

test("with changes switched off every burst and Restart is disabled, and nothing is sent", async () => {
  // A push-button burst runs, so Clear bursts shows too.
  const api = await open({ fixture: ["writes-off", "push-button-burst"] });
  const controls = [...document.querySelectorAll<HTMLButtonElement>(".burst-panel [data-write-control]")];
  expect(controls.map((control) => control.textContent)).toEqual(["Fans 15 min", "Humidify 5 min", "Vent bypass 10 min", "Clear bursts", "Restart controller"]);
  expect(controls.every((control) => control.disabled)).toBe(true);
  expect(writes(api)).toEqual([]);
});

test("until API-IRR-01 answers nothing is offered; if it fails the panel says so with Retry, and what runs still shows", async () => {
  const api = await open({ fixture: "push-button-burst", fail: { "API-IRR-01": 503 } });
  expect(document.querySelectorAll(".burst-panel [data-write-control]")).toHaveLength(0);
  expect(text(document.querySelector(".burst-panel .panel-error .fault"))).toBe("Can't tell whether the controller has the irrigation firmware yet.");
  expect(bursts()).toEqual(["Fans burst"]);
  const reads = requests(api, "API-IRR-01").length;
  press("Retry");
  await advance(0);
  expect(requests(api, "API-IRR-01")).toHaveLength(reads + 1);
});

test("while the controller is not reporting what runs reads unknown, never none", async () => {
  await open({ fixture: "controller-not-reporting" });
  expect(running()).toBe("Running: unknown");
  cleanup(); stopFixtureApi();
  await open({ fixture: "ingestor-not-running" });
  expect(running()).toBe("Running: unknown");
});

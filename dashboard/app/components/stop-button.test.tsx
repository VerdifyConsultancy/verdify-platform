// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// A line's Stop (#1136, redesign D-UX-06) against #1122's fixture API in process: one tap, no dialog, one request; filled
// only while the relay is on or unknown and a quiet outline otherwise; never disabled by the writes flag; the relay edge turns the card's state off and the Stop result names what can still start, with Disable
// for the line with the irrigation firmware and without it before. The clock is 10:42 America/Denver on 2026-11-02.
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { Now } from "../lib/operator-api";
import { fetchRead, readKey, useIrrigationRead, useNowRead } from "../lib/queries";
import { OPERATOR, UUID, advance, renderWithApi, sentAt, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import { READBACK_POLL_MS } from "../lib/writes";
import { StopButton } from "./stop-button";
import { text } from "./test-render";

afterEach(() => { cleanup(); stopFixtureApi(); });

const STILL_START = "Stopped every running valve. VPD misting, fog and the next scheduled run can still start on their own; to keep a line off, disable it.";
const STILL_START_NO_ENGINE = "Stopped every running valve. VPD misting and fog can still start on their own.";

/** The line's state over the shared API-IRR-01 read, and API-02 as the shell polls it, from which Stop notes what runs. */
function Card({ lineId }: { lineId: string }) {
  useNowRead();
  const status = useIrrigationRead();
  const line = status.data?.lines.find((item) => item.line_id === lineId);
  return <div><p data-testid="state">{line?.state.code ?? "—"}</p><StopButton lineId={lineId} /></div>;
}
async function open(lineId: string, options: FixtureOptions, session = OPERATOR) {
  const api = startFixtureApi(options);
  renderWithApi(<Card lineId={lineId} />, session);
  await advance(0);
  return api;
}
const stopButton = () => screen.queryByRole("button", { name: /^(Stop|Sending…)/ });
const toasts = () => [...screen.getByRole("status").querySelectorAll(".toast-body")].map((body) => [...body.querySelectorAll("p")].map((line) => text(line)).join(" "));

test("one tap on a running line: one API-IRR-07 {line_id} with no dialog, the relay edge reads OFF, and the toast offers Disable", async () => {
  const api = await open("L01", { fixture: "l01-running" });
  expect(screen.getByTestId("state").textContent).toBe("running");
  const stop = stopButton()!;
  expect(stop.className).toContain("btn-stop");
  expect(text(stop)).toBe("Stop Wall drip");
  fireEvent.click(stop);
  expect(text(stopButton())).toBe("Sending…");
  expect(document.querySelector("dialog")).toBeNull();
  await advance(0);
  const sent = writes(api);
  expect(sent).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-07", path: "/api/v1/irrigation/stop", body: { line_id: "L01" } })]);
  expect(sent[0].idempotency_key).toMatch(UUID);
  expect(text(document.querySelector("[data-stop-note]"))).toBe("Waiting for the valve to read off");
  // The card's read does not wait for the board's 15 s poll.
  await advance(READBACK_POLL_MS);
  await advance(1);
  expect(sentAt("API-IRR-01")).toContain(READBACK_POLL_MS);
  expect(screen.getByTestId("state").textContent).toBe("idle");
  expect(toasts()).toEqual([STILL_START]);
  expect(screen.getByRole("button", { name: "Disable Wall drip" })).toBeTruthy();
  expect(document.querySelector("[data-stop-note]")).toBeNull();
  expect(writes(api)).toHaveLength(1);
});

test("a Stop right after a Run: the card's read shows the line running before API-02 does, and the toast still offers Disable", async () => {
  const api = startFixtureApi();
  const { client } = renderWithApi(<Card lineId="L01" />);
  await advance(0);
  api.respond({ method: "POST", url: "/api/v1/irrigation/lines/L01/run", headers: {}, body: { minutes: 5 } });
  await act(async () => { await fetchRead(client, "API-IRR-01", {}); });
  await advance(1);
  expect(screen.getByTestId("state").textContent).toBe("running");
  expect(client.getQueryData<Now>(readKey("API-02"))?.running).toEqual([]);
  fireEvent.click(stopButton()!);
  await advance(0);
  await advance(READBACK_POLL_MS);
  await advance(1);
  expect(toasts()).toEqual([STILL_START]);
  expect(screen.getByRole("button", { name: "Disable Wall drip" })).toBeTruthy();
});

test("stays enabled and sends with changes switched off", async () => {
  const api = await open("L01", { fixture: ["l01-running", "writes-off"] }, { role: "operator", writesEnabled: false });
  expect(stopButton()?.hasAttribute("disabled")).toBe(false);
  fireEvent.click(stopButton()!);
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-07", body: { line_id: "L01" } })]);
  await advance(READBACK_POLL_MS);
  await advance(1);
  expect(screen.getByTestId("state").textContent).toBe("idle");
});

test("before the irrigation firmware: the same one request, and the toast names what can still start with no Disable", async () => {
  const api = await open("L04", { fixture: "before-engine" });
  fireEvent.click(stopButton()!);
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-07", body: { line_id: "L04" } })]);
  await advance(READBACK_POLL_MS);
  await advance(1);
  expect(toasts()).toEqual([STILL_START_NO_ENGINE]);
  expect(screen.queryByRole("button", { name: /^Disable/ })).toBeNull();
});

test("a viewer sees no Stop", async () => {
  await open("L01", { fixture: ["l01-running", "viewer-session"] }, { role: "viewer", writesEnabled: false });
  expect(stopButton()).toBeNull();
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(0);
});

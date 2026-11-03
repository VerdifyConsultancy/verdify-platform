// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Clear fault (#1136) against #1122's fixture API in process: one API-IRR-13 on a latch, the readback, no Clear on a
// back-off or a line the operation does not name, the words before the irrigation firmware, viewers and changes off.
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { IrrigationLine } from "../lib/operator-api";
import { useIrrigationRead } from "../lib/queries";
import { OPERATOR, UUID, advance, renderWithApi, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import { CLEAR_ARRIVES_TEXT, ClearFaultButton } from "./clear-fault-button";
import { renderUi, text } from "./test-render";

afterEach(() => { cleanup(); stopFixtureApi(); });

function Card({ lineId }: { lineId: string }) {
  const status = useIrrigationRead();
  const line = status.data?.lines.find((item) => item.line_id === lineId);
  if (!status.data || !line) return <p>Loading</p>;
  return <div><p data-testid="fault">{line.fault ?? "none"}</p><ClearFaultButton line={line} engine={status.data.engine} /></div>;
}
async function open(lineId: string, options: FixtureOptions, session = OPERATOR) {
  const api = startFixtureApi(options);
  renderWithApi(<Card lineId={lineId} />, session);
  await advance(0);
  return api;
}
const clear = () => screen.queryByRole("button", { name: /^(Clear fault|Sending…)/ });
const zone = (fault: IrrigationLine["fault"], kind = "mister"): Pick<IrrigationLine, "line_id" | "kind" | "fault"> => ({ line_id: "L07", kind, fault });

test("a latched mister: one API-IRR-13 {line_id} with no dialog, Sending… until the 202, then the latch is gone from API-IRR-01", async () => {
  const api = await open("L04", { fixture: "mister-fault" });
  expect(screen.getByTestId("fault").textContent).toBe("mister_fault");
  fireEvent.click(clear()!);
  expect(text(clear())).toBe("Sending…");
  fireEvent.click(clear()!);
  expect(document.querySelector("dialog")).toBeNull();
  await advance(0);
  const sent = writes(api);
  expect(sent).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-13", path: "/api/v1/irrigation/clear-fault", body: { line_id: "L04" } })]);
  expect(sent[0].idempotency_key).toMatch(UUID);
  expect(text(document.querySelector("[data-clear-note]"))).toBe("Waiting for the controller");
  // A read's answer reaches the page on the tick after it.
  await advance(5000);
  await advance(1);
  expect(screen.getByTestId("fault").textContent).toBe("none");
  expect(clear()).toBeNull();
  expect(writes(api)).toHaveLength(1);
});

test("a back-off shows no Clear, nor does a clear line or a fertilizer mister, which the operation does not name", () => {
  renderUi(<ClearFaultButton line={zone("backing_off")} engine />, OPERATOR);
  expect(document.body.textContent).toBe("");
  renderUi(<ClearFaultButton line={zone(null)} engine />, OPERATOR);
  expect(document.body.textContent).toBe("");
  renderUi(<ClearFaultButton line={zone("mister_fault", "fert_mister")} engine />, OPERATOR);
  expect(document.body.textContent).toBe("");
});

test("before the irrigation firmware a zone says when Clear fault arrives, with no button, for operators only", async () => {
  await open("L04", { fixture: "before-engine" });
  expect(text(document.querySelector("[data-arrives=clear-fault]"))).toBe(CLEAR_ARRIVES_TEXT);
  expect(clear()).toBeNull();
  cleanup(); stopFixtureApi();
  await open("L02", { fixture: "before-engine" });
  expect(document.querySelector("[data-arrives]")).toBeNull();
  cleanup(); stopFixtureApi();
  await open("L04", { fixture: "before-engine" }, { role: "viewer", writesEnabled: false });
  expect(document.querySelector("[data-arrives]")).toBeNull();
});

test("a viewer sees no Clear fault; with changes switched off it is disabled", async () => {
  await open("L04", { fixture: ["mister-fault", "viewer-session"] }, { role: "viewer", writesEnabled: false });
  expect(clear()).toBeNull();
  cleanup(); stopFixtureApi();
  await open("L04", { fixture: ["mister-fault", "writes-off"] }, { role: "operator", writesEnabled: false });
  expect(clear()?.hasAttribute("disabled")).toBe(true);
});

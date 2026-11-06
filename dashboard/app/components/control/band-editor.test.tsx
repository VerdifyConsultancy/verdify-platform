// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The band editor (#1334) against #1122's fixture API in process, with uPlot mocked: the fields start from the band as
// stored, an edit previews through API-23 with only the changed field, Save is one API-24 write, the night floor is
// refused beside its field, and a viewer sees the preview with no form.
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { FakeResizeObserver, MockPlot, media, setTokens } from "../../charts/fixtures/uplot-mock";
import { OPERATOR, advance, renderWithApi, requests, startFixtureApi, stopFixtureApi, writes } from "../../lib/test-fixture-api";
import { PREVIEW_DEBOUNCE_MS } from "../band-preview";
import { BandEditor, draftParams } from "./band-editor";

vi.mock("uplot", async () => ({ default: (await import("../../charts/fixtures/uplot-mock")).MockPlot }));

async function settle() {
  for (let pass = 0; pass < 6; pass++) {
    await advance(10);
    await act(async () => { await vi.dynamicImportSettled(); });
  }
}
async function mount(session = OPERATOR) {
  const api = startFixtureApi();
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  setTokens();
  renderWithApi(<BandEditor />, session, "/band");
  await settle();
  return api;
}
const queries = (api: ReturnType<typeof startFixtureApi>) => requests(api, "API-23").map((row) => new URL(row.path, "http://dashboard.invalid").searchParams.toString());
const field = (label: string, nth = 0) => screen.getAllByLabelText(label)[nth] as HTMLInputElement;
async function type(input: HTMLInputElement, value: string) {
  fireEvent.change(input, { target: { value } });
  await advance(PREVIEW_DEBOUNCE_MS);
  await settle();
}

beforeEach(() => { MockPlot.reset(); FakeResizeObserver.instances = []; });
afterEach(() => { cleanup(); stopFixtureApi(); document.documentElement.removeAttribute("style"); });

test("draftParams sends only the fields that changed, and nothing while one does not read as a number", () => {
  const base = { temp_day: "78.8", temp_night: "65.7" };
  expect(draftParams({ ...base }, base, {}, {})).toEqual({});
  expect(draftParams({ ...base, temp_night: "67.7" }, base, {}, {})).toEqual({ temp_night: 67.7 });
  expect(draftParams({ ...base, temp_night: "warm" }, base, {}, {})).toBeNull();
  expect(draftParams(base, base, { band_vpd_low_mid: "0.76" }, { band_vpd_low_mid: "0.74" })).toEqual({ anchor: ["band_vpd_low_mid:0.76"] });
});

test("an edit previews through API-23 and Save writes the changed anchors in one API-24 request", async () => {
  const api = await mount();
  const night = field("Night target (solar midnight)");
  expect(night.value).toBe("65.7");
  expect((screen.getByRole("button", { name: "Save band" }) as HTMLButtonElement).disabled).toBe(true);
  await type(night, "67.7");
  expect(queries(api).at(-1)).toBe("temp_night=67.7");
  expect(screen.getByText(/Save writes \d+ anchors in one change/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Reason (optional)"), { target: { value: "Warmer nights" } });
  fireEvent.click(screen.getByRole("button", { name: "Save band" }));
  await settle();
  const sent = writes(api);
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({ operationId: "API-24", body: { profile: { temp_night: 67.7 }, anchors: {}, manual: true, reason: "Warmer nights" } });
  // The fields start again from the band as stored, now with the new night target.
  expect(field("Night target (solar midnight)").value).toBe("67.7");
});

test("a night floor under 0.74 kPa shows beside its field and Save stays off", async () => {
  await mount();
  await type(field("Night range below"), "0.30");
  expect(screen.getAllByText(/it stays at least 0.74 kPa so the house dries out overnight/).length).toBeGreaterThan(0);
  expect((screen.getByRole("button", { name: "Save band" }) as HTMLButtonElement).disabled).toBe(true);
});

test("a viewer sees the preview and the guardrails, with no form", async () => {
  await mount({ role: "viewer", writesEnabled: false });
  expect(screen.queryByRole("button", { name: "Save band" })).toBeNull();
  expect(screen.queryByLabelText("Night target (solar midnight)")).toBeNull();
  expect(screen.getByText(/Guardrails 40/)).toBeTruthy();
});

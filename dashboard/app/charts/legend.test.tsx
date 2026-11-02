// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup } from "@testing-library/react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, expect, test, vi } from "vitest";
import { media } from "./fixtures/uplot-mock";
import { Legend, type LegendItem } from "./legend";

const ITEMS: LegendItem[] = [
  { key: "climate.temp_avg", label: "Indoor temperature", token: "--c-temp", shown: true },
  { key: "climate.outdoor_temp_f", label: "Outdoor temperature", token: "--c-outdoor", shown: false },
];
afterEach(() => { cleanup(); vi.unstubAllGlobals(); document.body.replaceChildren(); });

test("the server renders the wide legend, and a phone hydrates it without a mismatch, then shows the disclosure", async () => {
  // The server has no viewport.
  vi.stubGlobal("matchMedia", undefined);
  const html = renderToString(<Legend items={ITEMS} onToggle={() => {}} />);
  expect(html).toMatch(/^<div class="chart-legend">/);
  expect(html).not.toMatch(/style=/);
  vi.stubGlobal("matchMedia", media({ narrow: true }));
  const container = document.createElement("div");
  container.innerHTML = html;
  document.body.appendChild(container);
  const errors: unknown[] = [];
  await act(async () => { hydrateRoot(container, <Legend items={ITEMS} onToggle={() => {}} />, { onRecoverableError: (error) => errors.push(error) }); });
  expect(errors).toEqual([]);
  expect(container.querySelector("details.chart-legend > summary")!.textContent).toBe("Legend");
  expect([...container.querySelectorAll("button")].map((button) => button.getAttribute("aria-pressed"))).toEqual(["true", "false"]);
});

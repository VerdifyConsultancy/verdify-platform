// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { Kv } from "./kv";
import { renderUi, text } from "./test-render";

afterEach(cleanup);

test("facts as a .kv list; a missing value is \"—\" with its reason, never 0 or blank", () => {
  const { container } = renderUi(<Kv items={[
    { term: "Firmware", value: "2026.10.30.1200" },
    { term: "Heap free", value: 0 },
    { term: "Uptime", value: null, reason: "controller not reporting" },
    { term: "RSSI", value: undefined },
    { term: "Rate", value: Number.NaN },
  ]} />);
  expect(container.querySelector("dl")?.className).toBe("kv");
  expect([...container.querySelectorAll("dt")].map(text)).toEqual(["Firmware", "Heap free", "Uptime", "RSSI", "Rate"]);
  expect([...container.querySelectorAll("dd")].map(text)).toEqual(["2026.10.30.1200", "0", "— controller not reporting", "—", "—"]);
});

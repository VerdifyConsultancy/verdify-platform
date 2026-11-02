// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { Details } from "./details";
import { renderUi, text } from "./test-render";

afterEach(cleanup);

test("a native details whose summary is the 44 px target, closed unless asked", () => {
  const { container } = renderUi(<Details summary="All readings · sources, times, mode reason"><p>Every reading</p></Details>);
  const details = container.querySelector("details")!;
  expect(text(details.querySelector("summary"))).toBe("All readings · sources, times, mode reason");
  expect(details.open).toBe(false);
  expect(text(details)).toContain("Every reading");
  cleanup();
  expect(renderUi(<Details summary="Plan changes" open><p>6 settings</p></Details>).container.querySelector("details")?.open).toBe(true);
});

// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { Banner } from "./banner";
import { renderUi, text } from "./test-render";

afterEach(cleanup);

test("one line in the fault or warn tint, linking to the page that explains it", () => {
  const { container } = renderUi(<Banner tone="fault" link={{ to: "/equipment", label: "Equipment" }}>Controller not reporting since 06:10</Banner>);
  const banner = container.querySelector("p")!;
  expect(banner.className).toBe("notice notice-fault banner");
  expect(text(banner)).toBe("Controller not reporting since 06:10 Equipment ›");
  const link = screen.getByRole("link", { name: "Equipment" });
  expect(link.getAttribute("href")).toBe("/equipment");
  cleanup();
  expect(renderUi(<Banner tone="warn">Changes are switched off; reading still works</Banner>).container.querySelector("p")?.className).toBe("notice notice-warn banner");
});

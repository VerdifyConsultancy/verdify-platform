// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { CsvLink, DataTable, TimeCell } from "./data-table";

const { createObjectURL, revokeObjectURL } = URL;
let made: string[] = [];
beforeEach(() => {
  made = [];
  URL.createObjectURL = vi.fn(() => { made.push(`blob:${made.length + 1}`); return made[made.length - 1]; });
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => { cleanup(); URL.createObjectURL = createObjectURL; URL.revokeObjectURL = revokeObjectURL; });

test("the data table states its rows, builds them only once opened, and heads each row with its time", async () => {
  const rows = vi.fn(() => [[<TimeCell key="t" at={1_793_518_200} />, "64.2 °F", "No data 02:10–02:40: controller connection lost"]]);
  const { container } = render(<DataTable tables={[{ caption: "Indoor temperature · America/Denver", headers: ["Time", "Indoor temperature (°F)", "Note"], rows, count: 1 }]} />);
  expect(container.querySelector("summary")!.textContent).toBe("Data table · 1 row");
  expect(rows).not.toHaveBeenCalled();
  const details = container.querySelector("details")!;
  act(() => { details.open = true; details.dispatchEvent(new Event("toggle")); });
  const region = screen.getByRole("region", { name: "Indoor temperature · America/Denver" });
  expect(region.getAttribute("tabindex")).toBe("0");
  const time = region.querySelector("tbody th[scope=row] time")!;
  expect(time.textContent).toBe("11/01/2026 01:30 GMT-06:00");
  expect(time.getAttribute("datetime")).toBe("2026-11-01T07:30:00.000Z");
});

test("CSV of this window is a Blob URL behind <a download>, replaced when the data changes and revoked on unmount", () => {
  const build = vi.fn(() => "time (America/Denver)\r\n");
  const version = {};
  const { rerender, unmount } = render(<CsvLink filename="verdify-graphs.csv" build={build} version={version} />);
  const link = screen.getByRole("link", { name: "CSV of this window" });
  expect([link.getAttribute("href"), link.getAttribute("download")]).toEqual(["blob:1", "verdify-graphs.csv"]);
  rerender(<CsvLink filename="verdify-graphs.csv" build={build} version={version} />);
  expect(build).toHaveBeenCalledTimes(1);
  rerender(<CsvLink filename="verdify-graphs.csv" build={build} version={{}} />);
  expect(link.getAttribute("href")).toBe("blob:2");
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:1");
  unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:2");
});

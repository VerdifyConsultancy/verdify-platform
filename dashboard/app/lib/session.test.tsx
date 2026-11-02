// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { DEFAULT_SESSION, OperatorOnly, SessionProvider, useApiDown, useRole, useWritesEnabled } from "./session";

afterEach(cleanup);
function Probe() { return <p>{useRole()} · {String(useWritesEnabled())} · {String(useApiDown())}</p>; }

test("until the shell provides API-01, every page is a viewer with writes off", () => {
  expect(DEFAULT_SESSION).toEqual({ role: "viewer", writesEnabled: false, apiDown: false });
  expect(render(<><Probe /><OperatorOnly><button type="button">Save</button></OperatorOnly></>).container.textContent).toBe("viewer · false · false");
});

test("the provider passes role, the writes flag and the API-down state; OperatorOnly renders for operators only", () => {
  const { container } = render(<SessionProvider role="operator" writesEnabled apiDown><Probe /><OperatorOnly><button type="button">Save</button></OperatorOnly></SessionProvider>);
  expect(container.textContent).toBe("operator · true · trueSave");
  cleanup();
  const viewer = render(<SessionProvider role="viewer" writesEnabled><OperatorOnly><button type="button">Save</button></OperatorOnly></SessionProvider>);
  expect(viewer.container.querySelector("button")).toBeNull();
});

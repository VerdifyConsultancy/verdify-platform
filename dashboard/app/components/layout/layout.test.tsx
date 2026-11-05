// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The card system and layout primitives of redesign §3 and §4: ObjectRow, ListCard, SectionHeader, Page, ScrollRegion,
// and the Card and Panel heads, in their states, with no nested interactives and no style attribute served.
import { cleanup, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test } from "vitest";
import { SessionProvider } from "../../lib/session";
import { Badge } from "../badge";
import { Card } from "../card";
import { Panel, type PanelState } from "../panel";
import { renderUi, text } from "../test-render";
import { ListCard } from "./list-card";
import { ObjectRow } from "./object-row";
import { Page, ScrollRegion } from "./page";
import { Section, SectionHeader } from "./section-header";

afterEach(cleanup);
const markup = (ui: ReactElement, apiDown = false) => renderToStaticMarkup(<MemoryRouter><SessionProvider apiDown={apiDown}>{ui}</SessionProvider></MemoryRouter>);

test("ObjectRow: title, kind, one pill, the state sentence, a visual and meta, then primary, Stop and ⋯ at the end; never itself a link", () => {
  renderUi(<ObjectRow title="Wall drip" titleHref="/water#L01" tag={<Badge kind="clean" />} pill={<Badge kind="requested" />} state="Idle · last 07:31, 12 min · ok"
    visual={<span className="strip" />} meta="next 16:00 · 10 min" primary={<button type="button">Run…</button>} stop={<button type="button">Stop</button>}
    menu={<button type="button">⋯</button>} data={{ "data-line": "L01", "data-state": "idle" }} />);
  const row = screen.getByRole("article", { name: "Wall drip" });
  expect(row.getAttribute("data-line")).toBe("L01");
  expect(row.getAttribute("data-state")).toBe("idle");
  expect(row.tagName).toBe("ARTICLE");
  expect(within(row).getByRole("heading", { level: 3 }).querySelector("a")?.getAttribute("href")).toBe("/water#L01");
  expect(text(row.querySelector(".object-row-state"))).toBe("Idle · last 07:31, 12 min · ok");
  expect(row.querySelector(".object-row-meta")?.className).toBe("object-row-meta meta");
  expect(within(row.querySelector(".object-row-actions") as HTMLElement).getAllByRole("button").map(text)).toEqual(["Run…", "Stop", "⋯"]);
  expect(row.querySelectorAll(".object-row-pill [data-badge]")).toHaveLength(1);
  expect(row.closest("a")).toBeNull();
});

test("ObjectRow: an abnormal row has its rule; a running one its token; both drop while the API is down; li inside a list", () => {
  renderUi(<ul><ObjectRow as="li" title="Fan 2 relay stuck" tone="warn" /></ul>);
  expect(screen.getByRole("listitem").className).toBe("object-row tone-warn");
  cleanup();
  renderUi(<ObjectRow title="Center mister" tone="running" token="eq-mister" />);
  expect(screen.getByRole("article").className).toBe("object-row tone-running tone-eq-mister");
  cleanup();
  renderUi(<ObjectRow title="Center mister" tone="fault" />, { apiDown: true });
  expect(screen.getByRole("article").className).toBe("object-row");
  expect(markup(<ObjectRow title="x" tone="warn" state="s" />)).not.toMatch(/\sstyle=/);
});

test("Panel and Card heads: one pill after the title, actions, All … › and ⋯ at the right, a meta foot with the data", () => {
  renderUi(<Panel title="Needs someone" pill={<Badge kind="stale" />} thing="alerts" state="ready" link={{ to: "/activity", label: "All alerts" }} menu={<button type="button">⋯</button>}
    actions={<button type="button">Acknowledge all</button>} foot="Updated 20 s ago" tone="warn"><p>rows</p></Panel>);
  const panel = screen.getByRole("region", { name: "Needs someone" });
  expect(panel.className).toBe("card panel tone-warn");
  expect(text(panel.querySelector(".card-title-row"))).toBe("Needs someoneStale");
  expect(within(panel.querySelector(".card-head-end") as HTMLElement).getAllByRole("link").map((link) => [text(link), link.getAttribute("href")])).toEqual([["All alerts ›", "/activity"]]);
  expect(text(panel.querySelector(".card-foot"))).toBe("Updated 20 s ago");
  cleanup();
  renderUi(<Card title="Site today" pill={<Badge kind="manual" />} link={{ to: "/water", label: "Water" }} foot="SAF-4 resets at midnight" tone="fault"><p>body</p></Card>);
  expect(document.querySelector(".card")?.className).toBe("card tone-fault");
  expect(text(document.querySelector(".card-title-row"))).toBe("Site todayManual");
  expect(screen.getByRole("link", { name: /^Water/ }).getAttribute("href")).toBe("/water");
  expect(text(document.querySelector(".card-foot"))).toBe("SAF-4 resets at midnight");
  // An interactive card is one link holding no other control.
  cleanup();
  renderUi(<Card to="/graphs/zones?zone=south" title="South" />);
  expect(screen.getByRole("link").className).toBe("card card-link");
});

test("ListCard in every state at its reserved height: rows with the count, the empty line and its widening link, an error with Retry", () => {
  const states: PanelState[] = ["ready", "loading", "empty", "stale", "error", "busy"];
  for (const state of states) {
    const { container } = renderUi(<ListCard title="Needs someone" count={state === "ready" ? 2 : null} thing="alerts" state={state} link={{ to: "/activity", label: "All alerts" }}
      empty={{ message: "No open alerts", widen: { to: "/activity?resolved=24h", label: "3 resolved today" } }} observedAt="2026-11-02T17:41:40Z" onRetry={() => {}}>
      <ol><li>Fan 2 relay stuck</li></ol>
    </ListCard>);
    const panel = screen.getByRole("region", { name: state === "ready" ? "Needs someone · 2" : "Needs someone" });
    expect(panel.getAttribute("data-state")).toBe(state);
    if (state === "ready") expect(text(panel.querySelector(".list-count"))).toBe("· 2");
    if (state === "empty") expect(within(panel).getByRole("link", { name: "3 resolved today" }).getAttribute("href")).toBe("/activity?resolved=24h");
    if (state === "loading") expect(panel.querySelector(".skeleton")).not.toBeNull();
    if (state === "error") expect(within(panel).getByRole("button", { name: "Retry" })).toBeTruthy();
    if (state === "stale") expect(panel.querySelector("[data-badge=stale]")).not.toBeNull();
    expect(container.querySelector("[style]")).toBeNull();
    cleanup();
  }
});

test("SectionHeader: the only section heading, an h2 with its meta, chips and one All … › link; Page and ScrollRegion", () => {
  renderUi(<Page><Section header={<SectionHeader title="Watering minutes" id="minutes" meta="last 7 days" chips={<span>chips</span>} link={{ to: "/water/history", label: "All runs" }} />}><p>bars</p></Section>
    <ScrollRegion label="Week matrix"><table><tbody><tr><td>x</td></tr></tbody></table></ScrollRegion></Page>);
  const heading = screen.getByRole("heading", { level: 2 });
  expect([heading.textContent, heading.id]).toEqual(["Watering minutes", "minutes"]);
  expect(text(document.querySelector(".section-header .meta"))).toBe("last 7 days");
  expect(screen.getByRole("link", { name: /^All runs/ }).className).toBe("section-link");
  const region = screen.getByRole("region", { name: "Week matrix" });
  expect([region.className, region.getAttribute("tabindex")]).toEqual(["l-scroll-x", "0"]);
  expect(document.querySelector(".page > .page-section")).not.toBeNull();
  expect(markup(<SectionHeader title="t" link={{ to: "/", label: "All" }} />)).not.toMatch(/\sstyle=/);
});

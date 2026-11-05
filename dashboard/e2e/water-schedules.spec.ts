// SPDX-License-Identifier: MIT
// SCR-04 Water → Schedules (redesign §6) at 1440 and 390 px against the fixture API, at the screens' 10:42 on Monday
// 2026-11-02: the week strip, the day timeline by manifold with its blocks, valve ticks, pause band and caps, the block
// popover, a new schedule from an empty slot with the editor's checks before Save, the week matrix (which keeps
// `.week-table` with `tr[data-line] td[data-day]`), the agenda on phones and the 390 px day view with one day visible.
import type { Page } from "@playwright/test";
import { DESKTOP, PHONE, axe, expect, expectNoHorizontalScroll, load, test, useScreensClock } from "./support";

const timeline = (page: Page) => page.locator(".sch-day");
const timelineRow = (page: Page, line: string) => timeline(page).locator(`.sch-row[data-line="${line}"]`);
const popover = (page: Page) => page.locator("dialog.popover[open]");
const drawer = (page: Page) => page.locator("dialog.drawer[open]");

test("1440 px: today's timeline groups rows by manifold with night, the now rule, runs as blocks, the valve's ticks, a pause band and each row's cap", async ({ page, request }) => {
  await page.setViewportSize(DESKTOP);
  await useScreensClock(page, request, "schedules-day");
  await load(page, "/water/schedules");
  await expect(timeline(page)).toBeVisible();
  await expect(page.locator(".sch-agenda")).toBeHidden();
  await expect(timeline(page).locator("h2")).toHaveText("Monday, Nov 2 · today");
  await expect(timeline(page).locator(".sch-group-label")).toHaveText(["Drip manifold", "Mister manifold"], { useInnerText: false });
  const lines = await timeline(page).locator(".sch-row").evaluateAll((rows) => rows.map((node) => node.getAttribute("data-line")));
  expect(lines).toEqual(["L01", "L02", "L05", "L06", "L04", "L07", "L08", "L09", "L10"]);
  await expect(timelineRow(page, "L01").locator(".sch-pause")).toContainText("paused until 14:00 · jason");
  await expect(timelineRow(page, "L04").locator(".sch-tick")).not.toHaveCount(0);
  await expect(timelineRow(page, "L04").getByRole("button", { name: /^Center mister · Dawn drench, Mon 06:50/ })).toBeAttached();
  await expect(timelineRow(page, "L04").locator(".sch-cap")).toContainText("14 / 120 min");
  await expect(timelineRow(page, "L07").locator(".sch-row-note")).toHaveText("Schedule off for this zone");
  await expect(timelineRow(page, "L07").locator(".sch-block.sch-skipped")).toHaveCount(1);
  await expect(timelineRow(page, "L01").locator(".sch-now")).toBeVisible();
  // The status line names the next run, read the same way everywhere.
  await expect(page.locator(".page-status")).toContainText("next: Center mister Tue 06:50");
  await expectNoHorizontalScroll(page);
  await axe(page);
});

test("a block opens its popover with its run, rule and next starts, Edit and \"Pause Wall drip until after this run\"; Escape and Back close it", async ({ page, request }) => {
  await page.setViewportSize(DESKTOP);
  await useScreensClock(page, request, "schedules-paused");
  await load(page, "/water/schedules?day=2026-11-04");
  await timelineRow(page, "L01").locator(".sch-block").first().click();
  await expect(page).toHaveURL(/\/water\/schedules\?day=2026-11-04&block=11-\d+$/);
  await expect(popover(page).locator("h2")).toHaveText("Wall drip · Wall MWF");
  await expect(popover(page)).toContainText("Wed 4 Nov · 07:35 (sunrise + 1 h) · 12 min");
  await expect(popover(page)).toContainText("Mon Wed Fri · sunrise + 1 h");
  await expect(popover(page).getByRole("link", { name: "Edit schedule" })).toBeVisible();
  await expect(popover(page).getByRole("button", { name: "Pause Wall drip until after this run" })).toBeVisible();
  await axe(page);
  await page.keyboard.press("Escape");
  await expect(popover(page)).toHaveCount(0);
  await expect(page).toHaveURL(/\/water\/schedules\?day=2026-11-04$/);
  await timelineRow(page, "L01").locator(".sch-block").first().click();
  await expect(popover(page)).toBeVisible();
  await page.goBack();
  await expect(popover(page)).toHaveCount(0);
  await expect(page).toHaveURL(/\/water\/schedules\?day=2026-11-04$/);
});

test("an empty slot opens a new schedule prefilled with its line, the day and the time; too close reads before Save and the draft is outlined", async ({ page, request }) => {
  await page.setViewportSize(DESKTOP);
  await useScreensClock(page, request, "l01-schedule");
  await load(page, "/water/schedules?day=2026-11-04");
  const track = timelineRow(page, "L01").locator(".sch-track");
  const box = (await track.boundingBox())!;
  // 08:00, clear of Wall MWF's 07:35–07:47 block and 25 minutes after its start.
  await page.mouse.click(box.x + box.width * (8 / 24), box.y + box.height / 2);
  await expect(page).toHaveURL(/\/water\/schedules\?day=2026-11-04&line=L01&at=08%3A00&schedule=new$/);
  await expect(drawer(page).getByLabel("Clock time (HH:MM)")).toHaveValue("08:00");
  await expect(drawer(page).getByRole("button", { name: "Wednesday" })).toHaveAttribute("aria-pressed", "true");
  await expect(drawer(page).locator(".sch-checks")).toContainText(/Starts 25\smin after Wall MWF on Wall drip: schedules on one line need 30\smin/);
  await expect(timelineRow(page, "L01").locator(".sch-draft.sch-conflict")).toHaveCount(1);
  await axe(page);
  await drawer(page).getByRole("button", { name: "Cancel" }).click();
  await expect(drawer(page)).toHaveCount(0);
  await expect(page).toHaveURL(/\/water\/schedules\?day=2026-11-04$/);
});

test("view=week: the matrix keeps tr[data-line] td[data-day], marks each run and its minutes against the cap, and a cell opens its day", async ({ page, request }) => {
  await page.setViewportSize(DESKTOP);
  await useScreensClock(page, request, "schedules-paused");
  await load(page, "/water/schedules");
  await page.getByRole("list", { name: "View", exact: true }).getByRole("link", { name: "Week" }).click();
  await expect(page).toHaveURL(/\/water\/schedules\?view=week$/);
  const week = page.locator(".week-table");
  const cell = week.locator('tr[data-line="L01"] td[data-day="2026-11-04"]');
  await expect(cell.locator(".sch-mark.sch-planned")).toHaveCount(1);
  await expect(cell.locator(".sch-cell-total")).toHaveText("12 / 60");
  await expect(week.locator('tr[data-line="L01"] td[data-day="2026-11-02"] .sch-mark.sch-done')).toHaveCount(1);
  await expectNoHorizontalScroll(page);
  await cell.getByRole("link").click();
  await expect(page).toHaveURL(/\/water\/schedules\?view=day&day=2026-11-04$/);
  await expect(timeline(page).locator("h2")).toHaveText("Wednesday, Nov 4");
});

test("the week strip shows past outcomes and future counts side by side, and moves a week at a time inside what API-IRR-02 serves", async ({ page, request }) => {
  await page.setViewportSize(DESKTOP);
  await useScreensClock(page, request, "schedules-paused");
  await load(page, "/water/schedules");
  const strip = page.locator(".week-days");
  await expect(strip.getByRole("link")).toHaveCount(7);
  await expect(strip.getByRole("link", { name: /^Monday, Nov 2 \(today\): 2 ok/ })).toHaveAttribute("aria-current", "date");
  await expect(strip.getByRole("link", { name: /^Wednesday, Nov 4: 2 next/ })).toBeVisible();
  await page.getByRole("link", { name: "Previous week" }).click();
  await expect(page).toHaveURL(/\/water\/schedules\?day=2026-10-26$/);
  await expect(strip.getByRole("link", { name: /^Sunday, Nov 1: 1 ran ok/ })).toBeVisible();
  await strip.getByRole("link", { name: /^Sunday, Nov 1/ }).click();
  await expect(timelineRow(page, "L02").locator(".sch-block.sch-skipped")).toHaveAttribute("aria-label", /skipped, line paused by jason$/);
  await expect(page.getByRole("link", { name: "Previous week" })).toHaveCount(0);
});

test("the rules list: every schedule under its line, a disabled one muted, with who changed it last and its switch", async ({ page, request }) => {
  await page.setViewportSize(DESKTOP);
  await useScreensClock(page, request, "schedules-day");
  await load(page, "/water/schedules");
  const rules = page.locator(".sch-rules");
  await expect(rules.locator(".sch-count-words")).toHaveText("5 of 24 schedules");
  await expect(rules.locator(".sch-rule-group")).toHaveCount(4);
  const topUp = rules.locator('[data-slot="15"]');
  await expect(topUp).toHaveClass(/is-muted/);
  await expect(topUp.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  await expect(rules.locator('[data-slot="14"] .object-row-meta')).toContainText("Iris may adjust · changed by iris (AI)");
  await expect(rules.locator('[data-slot="11"] .object-row-state')).toHaveText(/^Mon Wed Fri · sunrise \+ 1\sh \(07:35 today\) · 12\smin$/);
});

test("390 px: the agenda is the default, by day, with NEXT and a skipped row hatched with its reason; the week strip's days are links", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, "schedules-day");
  await load(page, "/water/schedules");
  await expect(timeline(page)).toBeHidden();
  const agenda = page.locator(".sch-agenda");
  await expect(agenda.locator("h3").first()).toHaveText("Today · Mon 2 Nov");
  await expect(agenda.locator(".sch-next")).toHaveCount(1);
  await expect(agenda.locator('[data-line="L07"] .sch-agenda-row.sch-skipped').first()).toContainText("Skipped until Schedule is on");
  await expect(agenda.locator(".sch-agenda-pauses")).toHaveText("Wall drip: paused until 14:00 by jason");
  await expect(page.locator(".week-days").getByRole("link", { name: /^Wednesday/ })).toBeVisible();
  // The rules fold into a disclosure per line under the agenda.
  await expect(page.locator('.sch-rule-group[data-line="L01"]')).not.toHaveAttribute("open", "");
  await agenda.locator(".sch-agenda-row").first().click();
  await expect(page.locator("dialog.popover-sheet[open]")).toBeVisible();
  await axe(page);
  await page.keyboard.press("Escape");
  await expectNoHorizontalScroll(page);
});

test("390 px: view=day fits one day across the width with 6-hour ticks and no sideways scroll", async ({ page, request }) => {
  await page.setViewportSize(PHONE);
  await useScreensClock(page, request, "schedules-paused");
  await load(page, "/water/schedules");
  await page.getByRole("list", { name: "View, phone" }).getByRole("link", { name: "Day" }).click();
  await expect(page).toHaveURL(/\/water\/schedules\?view=day$/);
  await expect(timeline(page)).toBeVisible();
  await expect(timeline(page).locator("h2")).toHaveCount(1);
  await expect(timeline(page).locator(".sch-axis-tick:visible")).toHaveText(["00", "06", "12", "18", "24"]);
  await expect(timeline(page).locator(".sch-row")).toHaveCount(9);
  const [track, view] = await Promise.all([timelineRow(page, "L01").locator(".sch-track").boundingBox(), page.viewportSize()]);
  expect(track!.x + track!.width).toBeLessThanOrEqual(view!.width);
  await expectNoHorizontalScroll(page);
  await axe(page);
});

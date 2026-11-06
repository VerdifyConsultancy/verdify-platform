// SPDX-License-Identifier: MIT
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { Chips } from "../components/chips";
import { useClockValue } from "../components/freshness";
import { PanelError } from "../components/panel";
import { Popover } from "../components/popover";
import { Agenda } from "../components/schedule/agenda";
import { BlockPopover } from "../components/schedule/block-popover";
import { DayTimeline, sunWords } from "../components/schedule/day-timeline";
import {
  agendaDays, dayModel, entriesOf, fallbackCount, fallbackWindow, lineInfo, mondayOf, occurrenceKey, pauseNotes, ruleGroups, weekCells, weekdayCode, type DayBlock,
} from "../components/schedule/model";
import { RulesList } from "../components/schedule/rules-list";
import { WeekMatrix } from "../components/schedule/week-matrix";
import { WeekStrip, weekStep } from "../components/schedule/week-strip";
import { ScheduleEditor } from "../components/schedule-editor";
import { dateShift, dayTitle, localDate } from "../lib/format";
import type { IrrigationLine } from "../lib/operator-api";
import { useApiRead, useSchedulesRead } from "../lib/queries";
import { previewSchedule, type ScheduleDraft } from "../lib/schedule-preview";
import { viewMeta } from "../lib/sections";
import { useRole } from "../lib/session";
import { EDITOR_PARAMS, dayStatesParams, localDay, newScheduleUrl, toMinute, useWaterOutlet, waterUrl } from "../lib/water";

export const meta = () => viewMeta("water-schedules");

const PATH = "/water/schedules";
const NO_LINES: readonly IrrigationLine[] = [];
/** The days API-IRR-02 serves around today: the 7 before (`past_days=7`) and the 6 after. */
const PAST_DAYS = 7;
const AHEAD_DAYS = 6;
/** The agenda's length from its first day. */
const AGENDA_DAYS = 3;
type View = "day" | "week" | "agenda";
const VIEWS: readonly View[] = ["day", "week", "agenda"];
const VIEW_WORDS: Readonly<Record<View, string>> = { day: "Day", week: "Week", agenda: "Agenda" };
const DATE = /^\d{4}-\d\d-\d\d$/;

/** The key's long form (redesign §6.3); the short legend is always on show under the timeline's axis (#1347). */
function ScheduleKey() {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  return <>
    <button type="button" className="btn sch-key-button" aria-haspopup="dialog" aria-expanded={anchor !== null} onClick={(event) => setAnchor(anchor ? null : event.currentTarget)}>Key</button>
    <Popover open={anchor !== null} anchor={anchor} onClose={() => setAnchor(null)} title="Key" className="sch-key-pop">
      <ul className="sch-key">
        <li><span className="sch-swatch sch-done sch-eq-water" aria-hidden="true" />ran (solid)</li>
        <li><span className="sch-swatch sch-planned sch-eq-water" aria-hidden="true" />planned (dashed)</li>
        <li><span className="sch-swatch sch-skipped" aria-hidden="true" />will not run, or skipped (hatched)</li>
        <li><span className="sch-swatch sch-missed" aria-hidden="true" />missed (fault outline)</li>
        <li><span className="sch-swatch sch-unknown" aria-hidden="true" />outcome unknown</li>
        <li><span className="sch-swatch sch-conditional" aria-hidden="true" />backup misting window: runs only if the VPD sensors fail (dotted); "not needed" once it passed without running</li>
        <li><span className="sch-swatch sch-planned sch-capped" aria-hidden="true" />ends at the daily cap (amber outline)</li>
        <li><span className="sch-swatch sch-tick-swatch" aria-hidden="true" />valve open, VPD misting included (ticks)</li>
        <li><span className="sch-swatch sch-legend-day" aria-hidden="true" />day, sunrise to sunset (white) · <span className="sch-swatch sch-night-swatch" aria-hidden="true" />night (shaded), on every lane, a disabled one too</li>
        <li><span className="sch-swatch sch-done sch-eq-water" aria-hidden="true" />clean water · <span className="sch-swatch sch-done sch-eq-fert" aria-hidden="true" />fertilized, in the same lane · <span className="sch-swatch sch-done sch-cause-prep" aria-hidden="true" />prewet and flush on the clean side · <span className="sch-swatch sch-done sch-eq-heat1" aria-hidden="true" />backup misting that ran</li>
      </ul>
      <p className="meta">MANUAL: Iris leaves it alone ("Let Iris change this" is off). Iris may adjust: Iris can change it. People and other agents can always change it.</p>
    </Popover>
  </>;
}

/**
 * SCR-04 Schedules (`/water/schedules`, redesign §6): the week strip on top, then the day timeline (the default on desktop),
 * the week matrix (`view=week`, desktop) or the agenda (the default on phones), then the rules list. API-IRR-02 is read
 * once with `past_days=7`, so past and future sit side by side (every 60 s, 5 s while a schedule settles); API-05 adds
 * the valve's open time on the day shown, up to today. `day=` and `view=` are in the URL, and so are the block popover
 * (`?block=`) and the editor (`?schedule=new|<slot>` with its prefill `line`, `at` and `copy`): opening pushes and
 * closing replaces, so Back closes them. Viewers get no editor, [+], switch, Edit or pause, whatever the URL says.
 */
export default function WaterSchedules() {
  const { irrigation, setSummary } = useWaterOutlet();
  const [search, setSearch] = useSearchParams();
  const navigate = useNavigate();
  const operator = useRole() === "operator";
  const agenda = useSchedulesRead({ past_days: PAST_DAYS });
  const today = useClockValue(localDay);
  const now = useClockValue(toMinute);
  const status = irrigation.data ?? null;
  const lines = status?.lines ?? NO_LINES;
  const info = useMemo(() => lineInfo(lines), [lines]);
  const [draft, setDraft] = useState<ScheduleDraft | null>(null);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);

  // ------------------------------------------------------------------------------------------ the URL
  const first = dateShift(today, -PAST_DAYS);
  const last = dateShift(today, AHEAD_DAYS);
  const dayParam = search.get("day");
  const day = dayParam && DATE.test(dayParam) && dayParam >= first && dayParam <= last ? dayParam : today;
  const viewParam = search.get("view");
  const view = (VIEWS as readonly string[]).includes(viewParam ?? "") ? viewParam as View : null;
  const editing = operator ? search.get("schedule") : null;
  const slotId = editing === "new" || editing === null ? null : Number(editing);
  const to = useCallback((changes: Readonly<Record<string, string | null>>) => waterUrl(PATH, search, changes), [search]);
  const editTo = useCallback((slot: number) => to({ schedule: String(slot), block: null, line: null, at: null, copy: null }), [to]);
  const newTo = useCallback((lineId: string, clock?: string) => newScheduleUrl(search, { line: lineId, at: clock ?? null, day: dayParam }), [search, dayParam]);
  const copyTo = useCallback((slot: number) => newScheduleUrl(search, { copy: String(slot) }), [search]);
  const close = useCallback(() => setSearch((current) => {
    const next = new URLSearchParams(current);
    for (const name of EDITOR_PARAMS) next.delete(name);
    return next;
  }, { replace: true, preventScrollReset: true }), [setSearch]);
  const openBlock = useCallback((key: string, opener: HTMLElement) => {
    setAnchor(opener);
    setSearch((current) => {
      const next = new URLSearchParams(current);
      next.set("block", key);
      return next;
    }, { preventScrollReset: true });
  }, [setSearch]);
  const closeBlock = useCallback(() => setSearch((current) => {
    const next = new URLSearchParams(current);
    next.delete("block");
    return next;
  }, { replace: true, preventScrollReset: true }), [setSearch]);

  // ------------------------------------------------------------------------------------------ the model
  const preview = editing !== null && draft && agenda.data
    ? previewSchedule(draft, { agenda: agenda.data, lines, engine: status ? status.engine : null, today, now })
    : null;
  const entries = useMemo(() => (agenda.data ? entriesOf(agenda.data, preview, now) : []), [agenda.data, preview, now]);
  // While the editor is open the timeline shows the next day its draft runs (redesign §6.5).
  const draftDay = preview?.blocks.length ? localDate(preview.blocks[0].occurrence.planned_start) : null;
  const shown = draftDay && draftDay >= first && draftDay <= last ? draftDay : day;
  // A day ahead has no valve time yet: it reads today's window, which Today shares, rather than an empty one.
  const lanes = useApiRead("API-05", dayStatesParams(shown > today ? today : shown), { intervalMs: 60_000 });
  const model = agenda.data ? dayModel({ day: shown, today, now, agenda: agenda.data, lines, lanes: shown > today ? null : lanes.data ?? null, entries }) : null;
  const monday = mondayOf(day);
  const cells = agenda.data ? weekCells(monday, { agenda: agenda.data, lines, today, first, last }) : [];
  const days = useMemo(() => agendaDays(entries, { first: day, days: AGENDA_DAYS, last, today, now, lines }), [entries, day, last, today, now, lines]);
  const groups = agenda.data ? ruleGroups(agenda.data.schedules, lines, agenda.data.solar.find((item) => item.date === today)) : [];
  // The fallback windows ahead in the days served (they run only if VPD sensing fails): the header's status line counts
  // them beside "no run planned this week", as the week strip and the today strips do.
  const fallbacks = agenda.data ? agenda.data.occurrences.filter((occurrence) => Date.parse(occurrence.planned_start) > now
    && fallbackWindow(occurrence, agenda.data?.schedules.find((schedule) => schedule.slot_id === occurrence.slot_id)) === "ahead").length : 0;
  const fallbackWords = fallbacks > 0 ? fallbackCount(fallbacks) : null;
  useEffect(() => {
    setSummary?.(fallbackWords);
    return () => setSummary?.(null);
  }, [setSummary, fallbackWords]);
  const blockKey = search.get("block");
  // The popover reads the saved occurrence, which an open edit's draft may hide from the views.
  const blockEntry = useMemo(() => (blockKey && agenda.data ? entriesOf(agenda.data, null, now).find((entry) => occurrenceKey(entry.occurrence) === blockKey) ?? null : null), [blockKey, agenda.data, now]);
  // Edit from a popover opens the editor over it, so closing the editor comes back to the popover and its Edit.
  const popEditTo = useCallback((slot: number) => to({ schedule: String(slot), line: null, at: null, copy: null }), [to]);
  // A popover opened from a link finds its block on the page to hang from.
  useEffect(() => {
    if (!blockKey || (anchor && anchor.isConnected && anchor.getAttribute("data-block") === blockKey)) return;
    // Keys are "<slot>-<epoch seconds>": nothing in them needs escaping.
    const all = [...document.querySelectorAll<HTMLElement>(`[data-block="${blockKey}"]`)];
    const found = all.find((element) => element.getClientRects().length > 0) ?? all[0] ?? null;
    if (found) setAnchor(found);
  }, [blockKey, anchor, agenda.data]);

  const onBlock = (block: DayBlock, opener: HTMLElement) => openBlock(block.blockKey, opener);
  const onSlot = operator ? (lineId: string, clock: string) => navigate(newScheduleUrl(search, { line: lineId, at: clock, day: shown === today ? dayParam : shown }), { preventScrollReset: true }) : undefined;
  const dayTo = (date: string) => to({ day: date === today ? null : date, block: null });
  const viewTo = (next: View) => to({ view: next, block: null });
  const desktopView = view ?? "day";
  const phoneView = view === "day" ? "day" : "agenda";
  const isToday = shown === today;

  const head = model && <>
    <div className="sch-day-title">
      <h2>{dayTitle(shown).replace(/, \d{4}$/, "")}{isToday && <span className="sch-day-word"> · today</span>}</h2>
      {sunWords(model.solar) && <p className="meta">{sunWords(model.solar)}</p>}
    </div>
    <nav className="sch-day-nav" aria-label="Day">
      {shown > first ? <Link className="button-link" to={dayTo(dateShift(shown, -1))} preventScrollReset><span aria-hidden="true">‹</span><span className="screen-reader">Previous day</span></Link> : <span className="button-link is-off" aria-hidden="true">‹</span>}
      <Link className="button-link" to={dayTo(today)} preventScrollReset aria-current={isToday ? "date" : undefined}>Today</Link>
      {shown < last ? <Link className="button-link" to={dayTo(dateShift(shown, 1))} preventScrollReset><span aria-hidden="true">›</span><span className="screen-reader">Next day</span></Link> : <span className="button-link is-off" aria-hidden="true">›</span>}
    </nav>
  </>;
  const timeline = model && <DayTimeline model={model} now={now} head={head} label={`Runs on ${dayTitle(shown)} by line`} selected={blockKey}
    onBlock={onBlock} newTo={operator ? newTo : undefined} onSlot={onSlot} />;
  const agendaView = <section className="sch-agenda-view" aria-labelledby="sch-agenda-title">
    <h2 id="sch-agenda-title" className="screen-reader">Agenda from {dayTitle(day)}</h2>
    <Agenda days={days} pauses={pauseNotes(lines, today, now)} selected={blockKey} onOpen={(row, opener) => openBlock(row.blockKey, opener)} />
  </section>;
  const weekDaysList = Array.from({ length: 7 }, (_, index) => dateShift(monday, index));

  return <div className="page sch-page">
    {agenda.isError && <PanelError thing="schedules" onRetry={() => void agenda.refetch()} />}
    {agenda.data
      ? <>
        <div className="page-toolbar sch-bar">
          <WeekStrip cells={cells} selected={day} dayTo={dayTo} previousTo={(() => { const step = weekStep(day, monday, -1, first, last); return step ? dayTo(step) : null; })()}
            nextTo={(() => { const step = weekStep(day, monday, 1, first, last); return step ? dayTo(step) : null; })()} />
          <div className="sch-views">
            <div className="sch-views-desktop"><Chips small label="View" items={VIEWS.map((item) => ({ key: item, label: VIEW_WORDS[item], to: viewTo(item), current: desktopView === item, keepScroll: true }))} /></div>
            <div className="sch-views-phone"><Chips small label="View, phone" items={(["agenda", "day"] as const).map((item) => ({ key: item, label: VIEW_WORDS[item], to: viewTo(item), current: phoneView === item, keepScroll: true }))} /></div>
            <ScheduleKey />
          </div>
        </div>
        {view === null && <>
          <div className="sch-only-desktop">{timeline}</div>
          <div className="sch-only-phone">{agendaView}</div>
        </>}
        {view === "day" && timeline}
        {view === "agenda" && agendaView}
        {view === "week" && <>
          <section className="sch-only-desktop sch-week-view" aria-labelledby="sch-week-title">
            <h2 id="sch-week-title" className="sch-week-title">Week of {dayTitle(monday).replace(/, \d{4}$/, "")}</h2>
            <WeekMatrix days={weekDaysList} today={today} entries={entries} info={info} dayTo={(date) => to({ day: date === today ? null : date, view: "day", block: null })} />
          </section>
          <div className="sch-only-phone">{agendaView}</div>
        </>}
        <RulesList groups={groups} agenda={agenda.data} program={status} today={today} now={now} editTo={editTo} newTo={(lineId) => newTo(lineId)} copyTo={copyTo} />
        <BlockPopover entry={blockEntry} anchor={anchor} onClose={closeBlock} agenda={agenda.data} info={info} program={status} now={now} editTo={operator ? popEditTo : undefined} />
      </>
      : !agenda.isError && <div className="sch-loading" aria-busy="true"><div className="skeleton sch-skeleton-strip" aria-hidden="true" /><div className="skeleton skeleton-lg" aria-hidden="true" /><p className="screen-reader">Loading the schedules</p></div>}
    {editing !== null && agenda.data && <ScheduleEditor key={`${editing}-${search.get("line") ?? ""}-${search.get("at") ?? ""}-${search.get("copy") ?? ""}`} slotId={slotId}
      schedule={agenda.data.schedules.find((row) => row.slot_id === slotId) ?? null} agenda={agenda.data} status={status} today={today} now={now} onClose={close} onDraft={setDraft}
      prefill={{ lineId: search.get("line"), weekday: search.get("line") || search.get("at") ? weekdayCode(day) : null, clock: search.get("at"), copy: agenda.data.schedules.find((row) => String(row.slot_id) === search.get("copy")) ?? null }}
      onSaved={() => close()} />}
  </div>;
}

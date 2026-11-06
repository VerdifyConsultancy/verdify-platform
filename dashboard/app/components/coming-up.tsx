// SPDX-License-Identifier: MIT
/**
 * "Running and next" on Now (redesign §5.1; screens SCR-01, US-NOW-03, US-NOW-05), `#coming-up`: what runs now, then
 * the hours around now.
 * - "Running now" at every width: a row per running relay and burst (`now/running.tsx`, `[data-chip]`).
 * - From 861 px, one `TimeTrack` over the last 6 h and the next 12 h: heat, vent and fans, fog and mist, the drips and the
 *   main and grow lights from API-05 (read only once the track nears the screen), the runs ahead from API-IRR-02, each
 *   light's window from its API-09 settings and its intent from API-13 (#1340), Iris's triggers and the plan's changes
 *   from API-07, and sunrise and sunset with night washed. The planner's and the lights' row names are `Term`s (#1339).
 * - At 860 px and below the same future as the "Next 12 h" list (`[data-coming]`): runs, the lights' windows, plan
 *   changes, the next Iris triggers and the sun. Both render and the stylesheet shows one, so hydration never reflows them.
 * - "Later": each line with no run in the next 12 h.
 * API-07 (24 h), API-IRR-02 (7 days) and API-09 poll every 60 s and API-13 every 15 s; each part fails alone and a failed
 * read says so, never "none". Reads only.
 */
import { useRef, type ReactNode } from "react";
import type { NowAnswer } from "../lib/now-answer";
import type { GlossaryId } from "../lib/glossary";
import type { Equipment, IrrigationStatus, Now, Plan, ScheduleAgenda, Setting } from "../lib/operator-api";
import { READ_INTERVAL_MS, panelRead, useApiRead, useEquipmentRead, useSettingsRead, useStatesWindowRead, type PanelRead } from "../lib/queries";
import { useMe } from "../lib/shell";
import { Badge } from "./badge";
import { Button } from "./button";
import { cls } from "./common";
import { TimeTrack } from "./data/time-track";
import { useClockValue } from "./freshness";
import { NEXT_H, PAST_H, ROW_LABELS, itemTime, laterText, nextItems, nextWindow, nights, trackRows, type NextItem, type NextWindow, type RowKey } from "./now/next";
import { useNearViewport } from "./now/reads";
import { RunningNow } from "./now/running";
import { Panel } from "./panel";
import { Term } from "./term";

const PLAN_PARAMS = { hours: 24 } as const;
const AGENDA_PARAMS = { days: 7 } as const;
export const NOTHING_NEXT = "Nothing planned in the next 12 h";
export const RUNS_BEFORE_ENGINE = "Schedules run with the irrigation firmware";
export const TRACK_LABEL = "Running and next: the last 6 hours and the next 12 hours";

/** A read's failure in one line, with Retry. */
function Failed({ what, onRetry }: { what: string; onRetry: () => void }) {
  return <p className="fault now-next-failed">Couldn't load {what}. <Button onClick={onRetry}>Retry</Button></p>;
}

/** The rows whose names need a definition: the planner's two and the lights, each a `Term` from the glossary. */
const ROW_TERMS: Partial<Record<RowKey, GlossaryId>> = { iris: "iris_triggers", plan: "plan_changes", light_main: "main_light", light_grow: "grow_light" };
const rowLabel = (key: string): ReactNode => {
  const term = ROW_TERMS[key as RowKey];
  return term ? <Term id={term}>{ROW_LABELS[key as RowKey]}</Term> : ROW_LABELS[key as RowKey] ?? key;
};
/** The rows API-05 draws the past of: the relays and the lights. */
const PAST_ROWS: ReadonlySet<string> = new Set<RowKey>(["heat", "air", "mist", "drip", "light_main", "light_grow"]);

type TrackReads = { plan: Plan | null; agenda: ScheduleAgenda | null; irrigation: IrrigationStatus | null; overrides: Now["overrides"]; settings: readonly Setting[] | null; equipment: Equipment["items"] | null };
/** The track, its six hours of API-05 read once it is near the screen. */
function Track({ input, window, near }: { input: TrackReads; window: NextWindow; near: boolean }) {
  return near ? <LiveTrack input={input} window={window} /> : <TimeTrack label={TRACK_LABEL} from={window.from} to={window.to} rows={[]} state="loading" />;
}
function LiveTrack({ input, window }: { input: TrackReads; window: NextWindow }) {
  const lanes = panelRead(useStatesWindowRead(PAST_H * 3600));
  const rows = trackRows({ ...input, lanes: lanes.data }, window).map((row) => ({ ...row, label: rowLabel(row.key) }));
  // Until the lanes answer, or while they fail, the past is unknown: the rows say so rather than read as off.
  const past = lanes.failed ? "the last 6 h: couldn't load" : lanes.pending ? "Loading the last 6 h" : null;
  const shown = past ? rows.map((row) => (PAST_ROWS.has(row.key) ? { ...row, note: past } : row)) : rows;
  return <>
    <TimeTrack label={TRACK_LABEL} from={window.from} to={window.to} now={window.now} night={nights(input.agenda?.solar, window)} rows={shown} />
    {lanes.failed && <Failed what="the last 6 h" onRetry={lanes.retry} />}
  </>;
}

/** One row of "Next 12 h": its time, what happens, and who planned it or why it will not run. */
function NextRow({ item, clock }: { item: NextItem; clock: number }) {
  const skipped = item.status === "skipped" || item.status === "blocked";
  return <li className={cls("now-next-item", skipped && "now-next-skipped")} data-coming={item.kind}>
    <time className="now-next-time" dateTime={new Date(item.at * 1000).toISOString()}>{itemTime(item.at, clock)}</time>
    <span className="now-next-what">{item.text}{item.ai && <> <Badge kind="ai" /></>}{item.note && <span className="meta"> · {item.note}</span>}</span>
  </li>;
}

export type RunningAndNextProps = {
  readonly now: Now | null;
  readonly answer: NowAnswer;
  readonly irrigation: PanelRead<IrrigationStatus>;
};

export function RunningAndNext({ now, answer, irrigation }: RunningAndNextProps) {
  const plan = panelRead(useApiRead("API-07", PLAN_PARAMS, { intervalMs: READ_INTERVAL_MS.plan }));
  const agenda = panelRead(useApiRead("API-IRR-02", AGENDA_PARAMS, { intervalMs: READ_INTERVAL_MS.schedules }));
  // The lights' windows and intent (#1340): a failed read draws neither, never a guess.
  const settingsRead = useSettingsRead();
  const equipmentRead = useEquipmentRead();
  const settings = settingsRead.error ? null : settingsRead.data?.items ?? null;
  const equipment = equipmentRead.error ? null : equipmentRead.data?.items ?? null;
  const me = useMe();
  // The clock's minute, on the server's clock: the hours around now are worth one render a minute.
  const clock = useClockValue((value) => Math.floor(value / 60_000) * 60_000);
  const trackRef = useRef<HTMLDivElement>(null);
  const near = useNearViewport(trackRef);
  const window = nextWindow(clock);
  const status = irrigation.failed ? null : irrigation.data;
  const beforeEngine = status?.engine === false;
  const items = nextItems({ plan: plan.data, agenda: agenda.data, settings, equipment }, window).filter((item) => !(beforeEngine && item.kind === "run"));
  const later = agenda.data && status ? laterText({ agenda: agenda.data, lines: status.lines }, window, clock, me) : null;
  const waiting = plan.pending || agenda.pending;
  const input = { plan: plan.data, agenda: agenda.data, irrigation: status, overrides: now?.overrides ?? [], settings, equipment };
  return <Panel id="coming-up" title="Running and next" thing="what is running and next" state="ready">
    <div className="now-next-now">
      <h3 className="now-sub">Running now</h3>
      <RunningNow now={now} answer={answer} irrigation={irrigation} />
    </div>
    <div ref={trackRef} className="now-track">
      <p className="now-sub now-track-caption">Last {PAST_H} h and next {NEXT_H} h</p>
      <Track input={input} window={window} near={near} />
    </div>
    <div className="now-next">
      <h3 className="now-sub">Next {NEXT_H} h</h3>
      {plan.failed && <Failed what="the plan" onRetry={plan.retry} />}
      {agenda.failed && <Failed what="the schedules" onRetry={agenda.retry} />}
      {waiting && !items.length
        ? <><div className="skeleton now-skeleton-list" aria-hidden="true" /><p className="screen-reader">Loading what comes next</p></>
        : items.length
          ? <ol className="now-next-list">{items.map((item) => <NextRow key={item.key} item={item} clock={clock} />)}</ol>
          : !plan.failed && !agenda.failed && <p className="meta">{NOTHING_NEXT}</p>}
      {beforeEngine && <p className="meta" data-coming="engine">{RUNS_BEFORE_ENGINE}</p>}
    </div>
    {later && <p className="meta now-later" data-coming="later"><span className="now-later-label">Later</span> {later}</p>}
  </Panel>;
}

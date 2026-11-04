// SPDX-License-Identifier: MIT
/**
 * "Running and next" on Now (redesign §5.1; screens SCR-01, US-NOW-03, US-NOW-05), `#coming-up`: what runs now, then
 * the hours around now.
 * - "Running now" at every width: a row per running relay and burst (`now/running.tsx`, `[data-chip]`).
 * - From 861 px, one `TimeTrack` over the last 6 h and the next 12 h: heat, vent and fans, fog and mist and the drips from
 *   API-05 (read only once the track nears the screen), the runs ahead from API-IRR-02, Iris's triggers and the plan's
 *   changes from API-07, and sunrise and sunset with night washed.
 * - At 860 px and below the same future as the "Next 12 h" list (`[data-coming]`): runs, plan changes, the next Iris
 *   triggers and the sun. Both render and the stylesheet shows one, so hydration never reflows them.
 * - "Later": each line with no run in the next 12 h.
 * API-07 (24 h) and API-IRR-02 (7 days) poll every 60 s; each part fails alone and a failed read says so, never "none".
 * Reads only.
 */
import { useRef } from "react";
import type { NowAnswer } from "../lib/now-answer";
import type { IrrigationStatus, Now, Plan, ScheduleAgenda } from "../lib/operator-api";
import { READ_INTERVAL_MS, panelRead, useApiRead, useStatesWindowRead, type PanelRead } from "../lib/queries";
import { useMe } from "../lib/shell";
import { Badge } from "./badge";
import { Button } from "./button";
import { cls } from "./common";
import { TimeTrack } from "./data/time-track";
import { useClockValue } from "./freshness";
import { NEXT_H, PAST_H, itemTime, laterText, nextItems, nextWindow, nights, trackRows, type NextItem, type NextWindow } from "./now/next";
import { useNearViewport } from "./now/reads";
import { RunningNow } from "./now/running";
import { Panel } from "./panel";

const PLAN_PARAMS = { hours: 24 } as const;
const AGENDA_PARAMS = { days: 7 } as const;
export const NOTHING_NEXT = "Nothing planned in the next 12 h";
export const RUNS_BEFORE_ENGINE = "Schedules run with the irrigation firmware";
export const TRACK_LABEL = "Running and next: the last 6 hours and the next 12 hours";

/** A read's failure in one line, with Retry. */
function Failed({ what, onRetry }: { what: string; onRetry: () => void }) {
  return <p className="fault now-next-failed">Couldn't load {what}. <Button onClick={onRetry}>Retry</Button></p>;
}

/** The track, its six hours of API-05 read once it is near the screen. */
function Track({ input, window, near }: { input: { plan: Plan | null; agenda: ScheduleAgenda | null; irrigation: IrrigationStatus | null; overrides: Now["overrides"] }; window: NextWindow; near: boolean }) {
  return near ? <LiveTrack input={input} window={window} /> : <TimeTrack label={TRACK_LABEL} from={window.from} to={window.to} rows={[]} state="loading" />;
}
function LiveTrack({ input, window }: { input: { plan: Plan | null; agenda: ScheduleAgenda | null; irrigation: IrrigationStatus | null; overrides: Now["overrides"] }; window: NextWindow }) {
  const lanes = panelRead(useStatesWindowRead(PAST_H * 3600));
  const rows = trackRows({ ...input, lanes: lanes.data }, window);
  // Until the lanes answer, or while they fail, the past is unknown: the rows say so rather than read as off.
  const past = lanes.failed ? "the last 6 h: couldn't load" : lanes.pending ? "Loading the last 6 h" : null;
  const shown = past ? rows.map((row, index) => (index < 4 ? { ...row, note: past } : row)) : rows;
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
  const me = useMe();
  // The clock's minute, on the server's clock: the hours around now are worth one render a minute.
  const clock = useClockValue((value) => Math.floor(value / 60_000) * 60_000);
  const trackRef = useRef<HTMLDivElement>(null);
  const near = useNearViewport(trackRef);
  const window = nextWindow(clock);
  const status = irrigation.failed ? null : irrigation.data;
  const beforeEngine = status?.engine === false;
  const items = nextItems({ plan: plan.data, agenda: agenda.data }, window).filter((item) => !(beforeEngine && item.kind === "run"));
  const later = agenda.data && status ? laterText({ agenda: agenda.data, lines: status.lines }, window, clock, me) : null;
  const waiting = plan.pending || agenda.pending;
  const input = { plan: plan.data, agenda: agenda.data, irrigation: status, overrides: now?.overrides ?? [] };
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

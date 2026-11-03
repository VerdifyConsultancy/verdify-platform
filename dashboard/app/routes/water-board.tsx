// SPDX-License-Identifier: MIT
import { useId, type ReactNode } from "react";
import { Link } from "react-router";
import { cls } from "../components/common";
import { useClockValue, useNow } from "../components/freshness";
import { LineCard } from "../components/line-card";
import { LineEnable } from "../components/line-enable";
import { OptionsButton } from "../components/line-options";
import { MisterAssociation } from "../components/mister-association";
import { PanelError } from "../components/panel";
import { ResumeLine } from "../components/pause-lines";
import { usePhone } from "../lib/narrow";
import type { IrrigationLine, IrrigationStatus } from "../lib/operator-api";
import { useSchedulesRead } from "../lib/queries";
import { UNAVAILABLE_TEXT, boardLines, isMaster, limitItems, localDay, misterLines, useWaterOutlet } from "../lib/water";

export const meta = () => [{ title: "Water · Verdify dashboard" }];

/** Where the SAF-4 figure leads: the site caps save through API-10 in the Settings drawer, not on Water (#1182). */
const SAF4_LINK = "/settings?edit=mister_daily_volume_max_gal";
/** The limits strip: SAF-4, the soft budget, each zone's minutes against its cap, the flow state, occupancy, fallback. */
function LimitsStrip({ status, today }: { status: IrrigationStatus; today: string }) {
  const now = useNow();
  return <ul className="limits-strip">
    {limitItems(status, now, today).map((item) => <li key={item.key} data-limit={item.key} className={cls(item.tone && `limit-${item.tone}`)}>
      {item.key === "saf4" ? <Link to={SAF4_LINK}>{item.text}</Link> : item.text}
    </li>)}
  </ul>;
}
/** A card's line controls (#1182): the enable switch, Resume while paused and Options. Viewers see none of them. */
function LineControls({ line }: { line: IrrigationLine }) {
  return <>
    <LineEnable line={line} />
    <ResumeLine line={line} />
    <OptionsButton line={line} />
  </>;
}

/** A Board section: headed above the cards on a wide screen; at phone width folded into a disclosure below them. */
function Section({ title, folded, children }: { title: string; folded: boolean; children: ReactNode }) {
  const id = useId();
  if (folded) return <details className="water-fold"><summary>{title}</summary>{children}</details>;
  return <section className="water-section" aria-labelledby={id}><h2 id={id} className="eyebrow">{title}</h2>{children}</section>;
}

/**
 * SCR-04 Board (`/water`): the limits strip, the three mister zones and a card per line, from the layout's API-IRR-01
 * read (schedule labels from API-IRR-02). At phone width running lines sort first and the limits and mister zones fold
 * below the cards. A failed status read says "Water status unavailable", never idle.
 */
export default function WaterBoard() {
  const { irrigation } = useWaterOutlet();
  const agenda = useSchedulesRead();
  const phone = usePhone();
  const today = useClockValue(localDay);
  const linesId = useId();
  const status = irrigation.data;
  if (irrigation.isError) return <PanelError thing="the water status" text={UNAVAILABLE_TEXT} onRetry={() => void irrigation.refetch()} />;
  if (!status) {
    return <div className="water-board" aria-busy="true">
      <div className="skeleton skeleton-sm" aria-hidden="true" /><div className="skeleton skeleton-md" aria-hidden="true" /><div className="skeleton skeleton-lg" aria-hidden="true" />
      <p className="screen-reader">Loading the water status</p>
    </div>;
  }
  const labels = new Map((agenda.data?.schedules ?? []).flatMap((schedule) => (schedule.label ? [[schedule.slot_id, schedule.label] as const] : [])));
  const master = status.lines.find(isMaster) ?? null;
  const limits = <Section title="Limits today" folded={phone}><LimitsStrip status={status} today={today} /></Section>;
  const misters = <Section title="Mister zones" folded={phone}>
    <div className="grid grid-3 mister-zones">{misterLines(status.lines).map((line) => <MisterAssociation key={line.line_id} line={line} status={status} today={today} />)}</div>
  </Section>;
  return <div className="water-board">
    {!phone && limits}
    {!phone && misters}
    <section className="water-section" aria-labelledby={linesId}>
      <h2 id={linesId} className="eyebrow">Lines</h2>
      <div className="grid grid-3 line-cards">
        {boardLines(status.lines, phone).map((line) => <LineCard key={line.line_id} line={line} master={line.line_id === "L02" ? master : null} engine={status.engine} program={status.program}
          today={today} scheduleLabels={labels} actions={<LineControls line={line} />} />)}
      </div>
    </section>
    {phone && limits}
    {phone && misters}
  </div>;
}

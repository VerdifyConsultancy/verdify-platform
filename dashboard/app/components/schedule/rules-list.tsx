// SPDX-License-Identifier: MIT
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { usePhone } from "../../lib/narrow";
import type { IrrigationStatus, Schedule, ScheduleAgenda } from "../../lib/operator-api";
import { OperatorOnly, useRole } from "../../lib/session";
import { agoWords, amountWords, daysWords, lineSyncWords, startWords } from "../../lib/water";
import { useProgramWrite } from "../../lib/writes";
import { isAiSource } from "../../lib/vocab";
import { Badge, ReadbackBadge } from "../badge";
import { Button } from "../button";
import { cls } from "../common";
import { Meter } from "../data/meter";
import { Ago } from "../freshness";
import { FieldError, Switch, describedBy, useFieldIds } from "../forms";
import { ObjectRow } from "../layout/object-row";
import { SectionHeader } from "../layout/section-header";
import { Menu } from "../menu";
import type { RuleGroup } from "./model";

/** CONFIRMED shows only while the newest write is under 10 minutes old (D-UX-07). */
export const CONFIRMED_FRESH_MS = 10 * 60_000;
const DEGRADED_WORDS = "only if VPD sensing is degraded";

/**
 * True while a pill of CONFIRMED is news (D-UX-07): the item's sync was seen requested or not confirmed on this page and
 * turned confirmed under 10 minutes ago, or its newest write (`updated_at`) is under 10 minutes old and confirmed.
 */
export function useFreshConfirmed(sync: string, updatedAt: string | null | undefined, now: number): boolean {
  const last = useRef(sync);
  const [turned, setTurned] = useState<number | null>(null);
  useEffect(() => {
    if (last.current !== "confirmed" && sync === "confirmed") setTurned(Date.now());
    last.current = sync;
  }, [sync]);
  if (sync !== "confirmed") return false;
  const written = updatedAt ? Date.parse(updatedAt) : Number.NaN;
  return (turned !== null && now - turned < CONFIRMED_FRESH_MS) || (Number.isFinite(written) && now - written < CONFIRMED_FRESH_MS);
}

/** The enable switch of a rule, for operators: `{enabled, manual: true}` through API-IRR-04 at once (an inline switch saves manual on). */
function RuleSwitch({ schedule }: { schedule: Schedule }) {
  const write = useProgramWrite("API-IRR-04");
  const ids = useFieldIds();
  const enabled = schedule.enabled ?? true;
  return <div className="sch-rule-switch">
    <Switch write label={<><span className="screen-reader">{schedule.label || `slot ${schedule.slot_id}`} </span>Enabled</>} checked={enabled} busy={write.sending}
      describedBy={describedBy(write.error && ids.errorId)} onChange={(on) => void write.send({ slot_id: schedule.slot_id }, { enabled: on, manual: true })} />
    {write.error && <FieldError id={ids.errorId}>{write.error.message}</FieldError>}
  </div>;
}

/** Delete in a rule's ⋯ menu: one tap, as the editor's Delete (API-IRR-05), no dialog. */
function RuleDelete({ schedule }: { schedule: Schedule }) {
  const write = useProgramWrite("API-IRR-05");
  return <>
    <Button write busy={write.sending} onClick={() => void write.send({ slot_id: schedule.slot_id }, undefined)}>Delete <span className="screen-reader">{schedule.label || `slot ${schedule.slot_id}`}</span></Button>
    {write.error && <p className="field-error" role="alert">{write.error.message}</p>}
  </>;
}

/** Who changed it last: "changed by iris (AI) 2 days ago"; the AI mark only for Iris or an agent. */
function ChangedBy({ schedule }: { schedule: Schedule }) {
  const by = schedule.updated_by;
  if (!by) return null;
  return <>changed by {by.name}{isAiSource(by.source) && <span className="sch-ai"> (AI)</span>} <Ago at={schedule.updated_at} words={agoWords} /></>;
}

export type RulesListProps = {
  groups: readonly RuleGroup[];
  agenda: Pick<ScheduleAgenda, "counts" | "solar">;
  program: Pick<IrrigationStatus, "engine" | "program"> | null;
  today: string;
  now: number;
  /** The editor's URLs: edit a schedule, a new one on a line, a copy of one. */
  editTo: (slotId: number) => string;
  newTo: (lineId: string) => string;
  copyTo: (slotId: number) => string;
};

/**
 * The rules list (redesign §6.4), always below the view: every schedule under its line in L01–L10 order, disabled ones
 * muted under theirs. A rule reads its days, its start in words with today's time, its amount, MANUAL or "Iris may
 * adjust", FERT, the degraded-only note, its one sync pill and who changed it last. Operators get the enabled switch, Edit
 * and ⋯ (Duplicate, Delete), and "+ Schedule" per line; viewers read the same rows. On phones each line folds into a
 * disclosure under the agenda.
 */
export function RulesList({ groups, agenda, program, today, now, editTo, newTo, copyTo }: RulesListProps) {
  const operator = useRole() === "operator";
  const phone = usePhone();
  const solar = agenda.solar.find((day) => day.date === today) ?? null;
  const { schedules: count, max_schedules: max } = agenda.counts;
  return <section className="sch-rules" aria-labelledby="sch-rules-title">
    <SectionHeader id="sch-rules-title" title="Schedules by line" meta={<span className="sch-count">
      <span className="sch-count-words">{count} of {max} schedules</span>
      <Meter label="Schedules on the controller" unit="" value={count} cap={max} decorative />
    </span>} />
    {groups.length === 0 && <p className="meta sch-rules-empty">No schedules yet. Lines water only from VPD control and Run now.</p>}
    {groups.map((group) => <details key={`${group.lineId}-${phone ? "fold" : "open"}`} className="sch-rule-group" data-line={group.lineId} open={!phone}>
      <summary><h3 className="sch-rule-line">{group.name}</h3><span className="meta"> · {group.schedules.length} {group.schedules.length === 1 ? "schedule" : "schedules"}</span></summary>
      <ul className="sch-rule-rows">
        {group.schedules.map((schedule) => <RuleRow key={schedule.slot_id} schedule={schedule} group={group} solar={solar} program={program} now={now}
          operator={operator} editTo={editTo} copyTo={copyTo} />)}
      </ul>
      <OperatorOnly>
        <p className="sch-rule-add"><Link className="button-link" to={newTo(group.lineId)} preventScrollReset data-write-control="true">+ Schedule <span className="screen-reader">on {group.name}</span></Link></p>
      </OperatorOnly>
    </details>)}
  </section>;
}

function RuleRow({ schedule, group, solar, program, now, operator, editTo, copyTo }: {
  schedule: Schedule; group: RuleGroup; solar: ScheduleAgenda["solar"][number] | null; program: RulesListProps["program"]; now: number; operator: boolean;
  editTo: (slotId: number) => string; copyTo: (slotId: number) => string;
}) {
  const fresh = useFreshConfirmed(schedule.sync, schedule.updated_at, now);
  const enabled = schedule.enabled ?? true;
  const fert = group.kind === "fert_drip" || group.kind === "fert_mister";
  const name = schedule.label || `Slot ${schedule.slot_id}`;
  const sync = schedule.sync;
  const syncWords = sync !== "confirmed" ? lineSyncWords(sync, program) : null;
  // One pill (redesign §4.4): NOT CONFIRMED, REQUESTED, then MANUAL, then CONFIRMED while it is news.
  const pill = sync === "requested" || sync === "not_confirmed" ? <ReadbackBadge state={sync} />
    : schedule.manual ? <Badge kind="manual" /> : fresh ? <Badge kind="confirmed" /> : null;
  const facts = [daysWords(schedule.days), startWords(schedule.start, solar), amountWords(schedule, group.kind, group.partner)];
  const meta = [
    schedule.manual ? (sync === "requested" || sync === "not_confirmed" ? "manual" : null) : "Iris may adjust",
    schedule.degraded_only ? DEGRADED_WORDS : null,
    !enabled ? "disabled: will not run" : null,
  ].filter(Boolean);
  return <ObjectRow as="li" headingLevel={4} className={cls("sch-rule", !enabled && "is-muted")} data={{ "data-slot": String(schedule.slot_id), "data-sync": sync }}
    title={name} tag={fert ? <Badge kind="fert" /> : undefined} pill={pill}
    state={facts.join(" · ")}
    meta={<>{meta.length > 0 && <>{meta.join(" · ")} · </>}<ChangedBy schedule={schedule} />{syncWords && <> · <span className={cls(sync === "not_confirmed" && "fault")}>{syncWords}</span></>}{fresh && schedule.manual && <> · confirmed</>}</>}
    primary={operator ? <>
      <RuleSwitch schedule={schedule} />
      <Link className="button-link" to={editTo(schedule.slot_id)} preventScrollReset data-write-control="true">Edit <span className="screen-reader">{name}</span></Link>
    </> : <span className="sch-rule-on">{enabled ? <Badge kind="on" /> : <Badge kind="off" />}</span>}
    menu={operator ? <Menu name={`More for ${name}`}>
      <Link to={copyTo(schedule.slot_id)} preventScrollReset data-write-control="true">Duplicate <span className="screen-reader">{name}</span></Link>
      <RuleDelete schedule={schedule} />
    </Menu> : undefined} />;
}

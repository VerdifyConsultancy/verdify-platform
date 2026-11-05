// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router";
import { TimeSeries } from "../charts/time-series";
import type { SettingSteps } from "../charts/types";
import { shownActor } from "../lib/activity";
import { localDate } from "../lib/format";
import { useFreshRead } from "../lib/graph-reads";
import type { DesiredSetting, Setting, SettingHistory } from "../lib/operator-api";
import { isAiSource } from "../lib/vocab";
import { needsLookback, revertTarget, type RevertTarget } from "../lib/revert";
import { useRole, useWritesEnabled } from "../lib/session";
import { keptValueText, unitLabel } from "../lib/setting-values";
import { useMe } from "../lib/shell";
import { errorFor, formError, useSettingWrite } from "../lib/writes";
import { Button } from "./button";
import { LocalTime } from "./common";
import { Mark } from "./control/marks";
import { FieldError, Reason, useFieldIds } from "./forms";
import { useClockValue, useServerSkew } from "./freshness";
import { Panel, PanelError } from "./panel";

/**
 * The setting drawer's change history and Revert (US-SET-06, screens SCR-05, redesign §5.10). API-12 over a chosen
 * window, read when the drawer opens and again when the setting's newest desired row changes: requests stepped by who
 * asked, the readback line, the plan ahead and the bounds, on #1126's `TimeSeries` with its tooltip, data table and CSV;
 * then the changes in words, newest first ("1.60 → 1.55 · Jason · manual · “Vandas drying” · Confirmed 08:01"). Revert
 * is one tap, with no dialog, and writes through API-10 or API-11 (`lib/revert.ts`), so a viewer or a read-only row sees
 * the history alone.
 */

const DAY_S = 86_400;
/** The window chips, in the URL as `hist=7d`; readbacks are kept 90 days. */
export const HISTORY_WINDOWS = [
  { id: "24h", label: "24 h", seconds: DAY_S },
  { id: "7d", label: "7 d", seconds: 7 * DAY_S },
  { id: "30d", label: "30 d", seconds: 30 * DAY_S },
  { id: "90d", label: "90 d", seconds: 90 * DAY_S },
] as const;
const DEFAULT_WINDOW = HISTORY_WINDOWS[1];
const LOOKBACK_WINDOW = HISTORY_WINDOWS[3];
/** API-12 reads this far past now, so the plan's future waypoints come back too. */
export const PLAN_AHEAD_S = 12 * 3600;
export const NO_READBACK_TEXT = "This setting has no readback";
const NO_METRICS: readonly never[] = [];

/** The `hist` chip the URL names; anything else reads the default. */
export const historyWindow = (id: string | null) => HISTORY_WINDOWS.find((item) => item.id === id) ?? DEFAULT_WINDOW;
const iso = (epochSeconds: number) => new Date(epochSeconds * 1000).toISOString();
const windowParams = (param: string, now: number, seconds: number) => ({ param, from: iso(now - seconds), to: iso(now + PLAN_AHEAD_S) });

/** The changes the entries list shows, newest first. */
export const CHANGES_SHOWN = 5;
/** One change in words: from, to, who, manual, the quoted reason, and its readback state with its time. */
export type ChangeEntry = { readonly id: number; readonly from: number | null; readonly row: DesiredSetting };
/** The window's desired rows as changes, newest first: each from the row before it (unknown for the window's first). */
export function changeEntries(desired: readonly DesiredSetting[], shown = CHANGES_SHOWN): ChangeEntry[] {
  return desired.map((row, index) => ({ id: row.id, row, from: index > 0 ? desired[index - 1].value : null })).reverse().slice(0, shown);
}

function ChangeLine({ entry, setting, me }: { entry: ChangeEntry; setting: Setting; me: ReturnType<typeof useMe> }) {
  const { row, from } = entry;
  const today = useClockValue((now) => localDate(new Date(now).toISOString()));
  const when = (at: string | null | undefined) => (at && Number.isFinite(Date.parse(at)) ? <LocalTime at={at} date={localDate(at) !== today} /> : null);
  const actor = shownActor(row.actor, me);
  const state = row.state === "confirmed" ? <>Confirmed {when(row.confirmed_at) ?? when(row.created_at)}</>
    : row.state === "requested" ? <>Requested {when(row.created_at)}</>
      : row.state === "not_confirmed" ? <>Not confirmed · sent {when(row.created_at)}</>
        : row.state === "released" ? <>Released {when(row.created_at)}</>
          : <>{row.state.replaceAll("_", " ")} · {when(row.created_at)}</>;
  return <li className="ctl-change" data-state={row.state}>
    {row.value === null
      ? <span className="ctl-change-value">Released to auto</span>
      : <span className="ctl-change-value">{from !== null && <>{keptValueText(from, setting)} <span aria-hidden="true">→</span><span className="screen-reader">to</span> </>}{keptValueText(row.value, setting)}</span>}
    {" · "}{actor.name}{isAiSource(actor.source) && <> · <Mark kind="ai" /></>}{row.value !== null && row.manual && " · manual"}
    {row.reason && <> · <span className="setting-reason">“{row.reason}”</span></>}
    {" · "}<span className="ctl-change-state">{state}</span>
  </li>;
}

/** The history as the chart draws it: its unit as the panel prints it, people by name, no device line without a readback. */
function drawnHistory(history: SettingHistory, me: ReturnType<typeof useMe>, noReadback: boolean): SettingHistory {
  return {
    ...history, unit: unitLabel(history.unit), readback: noReadback ? [] : history.readback,
    desired: history.desired.map((row) => ({ ...row, actor: shownActor(row.actor, me) })),
  };
}

export type SettingHistoryProps = {
  setting: Setting;
  /** The drawer's Manual box: Revert sends it, as Save does. */
  manual: boolean;
  /** A Revert the API took: the drawer closes, and the row reads REQUESTED, as after a Save. */
  onReverted: () => void;
  /** A crop-band anchor or width, whose previous value a release cannot bring back (`lib/revert.ts`). */
  band?: boolean;
  /** Save is out: Revert waits for it. */
  busy?: boolean;
  /** A Revert went out or came back: Save and Release wait for it in turn. */
  onSending?: (sending: boolean) => void;
};

export function SettingHistoryPanel({ setting, manual, onReverted, band = false, busy = false, onSending }: SettingHistoryProps) {
  // A viewer, or anyone on a read-only row, sees the history with no Revert.
  const canRevert = useRole() === "operator" && setting.writable;
  const [params] = useSearchParams();
  const { pathname } = useLocation();
  const skew = useRef(0);
  skew.current = useServerSkew();
  const me = useMe();
  const chosen = historyWindow(params.get("hist"));
  const newestId = setting.desired?.id ?? null;
  // The chart's now, on the server's clock: set when the drawer opens, and again when another write arrives while it is open.
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => { setNow(Math.floor((Date.now() - skew.current) / 1000)); }, [newestId]);
  const read = useFreshRead("API-12", now === null ? null : windowParams(setting.param, now, chosen.seconds));
  const answer = read.data ?? null;
  const current = answer !== null && !read.isPlaceholderData;
  // Revert's rule: a window holding only the newest row reads 90 days back once. The 90 d window already has.
  const wantOlder = canRevert && answer !== null && now !== null && chosen.id !== LOOKBACK_WINDOW.id && needsLookback(answer.desired);
  const older = useFreshRead("API-12", wantOlder ? windowParams(setting.param, now, LOOKBACK_WINDOW.seconds) : null);
  const revertFrom = wantOlder ? (older.isPlaceholderData ? null : older.data ?? null) : answer;
  const target = canRevert && revertFrom ? revertTarget(revertFrom, setting, me, { band }) : null;
  const noReadback = setting.readback_via === "none";
  const drawn = useMemo(() => (answer ? drawnHistory(answer, me, noReadback) : null), [answer, me, noReadback]);
  const unit = unitLabel(setting.unit);
  const steps: SettingSteps[] = drawn ? [{ history: drawn, label: setting.label, allRequests: true, format: (value) => keptValueText(value, setting) }] : [];
  const hrefOf = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("hist", id);
    return `${pathname}?${next.toString()}`;
  };
  const entries = answer ? changeEntries(answer.desired) : [];
  return <div className="drawer-history">
    <Panel title="Change history" eyebrow="Requests, readback and plan" headingLevel={3} thing="history" reserve="lg"
      state={answer ? "ready" : read.isError ? "error" : "loading"} onRetry={() => { void read.refetch(); }}
      actions={<ul className="chips chips-sm" aria-label="History window">
        {HISTORY_WINDOWS.map((item) => <li key={item.id}>
          <Link to={hrefOf(item.id)} replace preventScrollReset aria-current={item.id === chosen.id ? "true" : undefined}>{item.label}</Link>
        </li>)}
      </ul>}>
      {noReadback && <p className="state-note history-no-readback">{NO_READBACK_TEXT}</p>}
      {drawn && now !== null && <TimeSeries title={unit ? `${setting.label} · ${unit}` : setting.label} series={null} metrics={NO_METRICS} unit={unit} settings={steps} now={now}
        xRange={{ from: now - chosen.seconds, to: now + PLAN_AHEAD_S }} syncKey={`verdify:setting-history:${chosen.id}`} />}
      {answer && <div className="ctl-changes">
        <p className="eyebrow">Changes in this window</p>
        {entries.length
          ? <ol className="ctl-change-list">{entries.map((entry) => <ChangeLine key={entry.id} entry={entry} setting={setting} me={me} />)}</ol>
          : <p className="meta">No change was asked for in this window.</p>}
      </div>}
      {wantOlder && older.isError && <PanelError thing="older history" text="Couldn't check the older history for Revert." onRetry={() => { void older.refetch(); }} />}
      {target && <RevertControl key={target.text} setting={setting} target={target} manual={manual} onReverted={onReverted} onSending={onSending} disabled={busy || !current} />}
    </Panel>
  </div>;
}

/**
 * The reason, prefilled and editable, and the one button, which says what it does. Revert has its own write hook, so its
 * refusal shows here in words and never beside the drawer's value field; it sends nothing else.
 */
function RevertControl({ setting, target, manual, onReverted, onSending, disabled }: { setting: Setting; target: RevertTarget; manual: boolean; onReverted: () => void; onSending?: (sending: boolean) => void; disabled: boolean }) {
  const write = useSettingWrite(setting, { onSaved: onReverted });
  const writesEnabled = useWritesEnabled();
  const { sending } = write;
  useEffect(() => {
    if (!sending || !onSending) return;
    onSending(true);
    return () => onSending(false);
  }, [sending, onSending]);
  const [reason, setReason] = useState(target.reason);
  const ids = useFieldIds();
  const refusal = write.error ? formError(write.error, ["reason"]) : null;
  const revert = () => {
    const words = reason.trim() || null;
    void (target.kind === "value" ? write.save({ value: target.value, manual, reason: words }) : write.release({ reason: words }));
  };
  return <div className="drawer-revert">
    {/* The drawer is one form: Enter here must not press Save. */}
    <div onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }}>
      <Reason label="Revert reason" value={reason} onChange={setReason} error={errorFor(write.error, "reason")} disabled={!writesEnabled} />
    </div>
    <Button write onClick={revert} busy={write.sending} disabled={disabled} aria-describedby={refusal ? ids.errorId : undefined}>{target.text}</Button>
    {refusal && <FieldError id={ids.errorId}>{refusal}</FieldError>}
  </div>;
}

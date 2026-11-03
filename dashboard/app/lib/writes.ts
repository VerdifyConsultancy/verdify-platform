// SPDX-License-Identifier: MIT
/**
 * The write hooks every screen changes something through (screens §1.4; design system §4.3 to §4.5; architecture §5.4,
 * §5.5 and §5.7). Each write is one operator API call through the BFF's `/api/v1` proxy, so desired state and the
 * ingestor stay the only route to the device:
 *
 * - **S** `useSettingWrite`: API-10 and API-11, the three site caps included.
 * - **P** `useProgramWrite`: API-IRR-03, -04, -05 and -08.
 * - **C** `useCommand`: API-IRR-06, -07, -12, -13 and API-20, then a readback.
 * - **R** `useRecordWrite`: API-15 and API-16.
 * - `useStop`: API-IRR-07 and its result toast.
 *
 * One action, one key: `crypto.randomUUID()` when Save, Run, Stop or Acknowledge is pressed, used for that request only.
 * While it is out the action is busy (the control reads "Sending…" and is disabled), and a second press sends nothing.
 * A non-GET is never retried, and there is no Send again: the dispatcher re-sends on its own. Last write wins, and
 * `manual` is the only control flag: edit forms always send it (ticked unless unticked), inline switches send true,
 * and Run, Stop, Pause, Resume and Clear fault send none. The client checks only min and max; validation, snapping and
 * the readback state (`requested`, `confirmed`, `not_confirmed` after 5 min, `held`) are the server's.
 */
import { createElement, Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { CLIMATE_REPORTING_S, HEARTBEAT_FRESH_S } from "../components/alert-row";
import { Button } from "../components/button";
import { useToast, type ToastOptions } from "../components/toast";
import { formatNumber, shortTime } from "./format";
import {
  ApiError, SessionEnded, apiWrite, newIdempotencyKey, type Alert, type AlertPage, type BodyOf, type DesiredSetting, type Freshness,
  type IrrigationLine, type IrrigationStatus, type Now, type ParamsOf, type Parsed, type Problem, type ReadOperationId, type Setting,
  type WriteResult,
} from "./operator-api";
import { engineFlag, fetchRead, readKey, SETTLING_INTERVAL_MS } from "./queries";
import { keptValueText, outOfRange, rangeText } from "./setting-values";
import { hasWords, words } from "./words";
import { NOW_QUERY_KEY } from "./shell";

/** Every edit form's "Manual: Iris skips this" box starts ticked (Round 2 #8). */
export const MANUAL_DEFAULT = true;
/** A reason, note or resolution is one line of at most 500 characters. */
export const REASON_MAX = 500;
/** A pending command's readback, like a REQUESTED row, is read every 5 s. */
export const READBACK_POLL_MS = SETTLING_INTERVAL_MS;
/** A command whose readback has not changed within 30 s says why, or "Not started"; Stop gives its result then. */
export const COMMAND_WINDOW_MS = 30_000;

// ---------------------------------------------------------------------------------------------------- errors
/** A refusal or failure in words, with the field it belongs beside (`Problem.field`) and the bounds a 422 names. */
export type WriteError = {
  readonly status: number;
  readonly code: string;
  readonly field: string | null;
  readonly min: number | null;
  readonly max: number | null;
  readonly message: string;
};
/** What an action did: sent and accepted, or refused with its error. `error` null means nothing was sent (still busy). */
export type WriteOutcome<T> = { readonly ok: true; readonly data: T; readonly replayed: boolean } | { readonly ok: false; readonly error: WriteError | null };
/** The unit and step a field's numbers are written with, for "Must be 0.50–2.50 kPa". */
export type FieldUnits = { readonly unit?: string | null; readonly step?: number | null };

/**
 * A Problem in words. `out_of_range` reads "Must be 0.50–2.50 kPa" from `min`, `max` and the field's unit;
 * `writes_disabled` "Changes are switched off"; `unavailable` its detail ("arrives with the irrigation firmware"). Any
 * other code reads through `words("problem_code", code)`; until #1124 fills that family, a code without words reads as
 * the Problem's own detail (plain words), else raw.
 */
export function problemMessage(problem: Problem, units: FieldUnits = {}): string {
  if (problem.code === "out_of_range" && problem.min !== null && problem.min !== undefined && problem.max !== null && problem.max !== undefined) {
    return `Must be ${rangeText(problem.min, problem.max, units.unit, units.step)}`;
  }
  if (problem.code === "writes_disabled") return "Changes are switched off";
  if (problem.code === "unavailable") return problem.detail || problem.title;
  if (hasWords("problem_code", problem.code)) return words("problem_code", problem.code);
  return problem.detail || problem.code;
}

/** Any failure of a write as a `WriteError`: a refusal with its Problem, the session ended, or the API unreachable. */
export function writeError(caught: unknown, unitsOf: (field: string | null) => FieldUnits = () => ({})): WriteError {
  if (caught instanceof SessionEnded) return { status: 401, code: "unauthenticated", field: null, min: null, max: null, message: caught.message };
  if (caught instanceof ApiError) {
    const { problem } = caught;
    const field = problem.field ?? null;
    return { status: caught.status, code: problem.code, field, min: problem.min ?? null, max: problem.max ?? null, message: problemMessage(problem, unitsOf(field)) };
  }
  return { status: 0, code: "unknown", field: null, min: null, max: null, message: caught instanceof Error ? caught.message : String(caught) };
}

/** The error's words when it belongs beside `field`, else null. A refusal naming no field shows with the form. */
export function errorFor(error: WriteError | null | undefined, field: string): string | null {
  return error && error.field === field ? error.message : null;
}
/** The error's words when it names no field the form shows (`fields`), else null. */
export function formError(error: WriteError | null | undefined, fields: readonly string[]): string | null {
  return error && (error.field === null || !fields.includes(error.field)) ? error.message : null;
}

const refused = (field: string | null, message: string, code = "invalid_request"): WriteError => ({ status: 0, code, field, min: null, max: null, message });
const tooLong = (field: string, text: string | null | undefined) => (text && text.length > REASON_MAX ? refused(field, `At most ${REASON_MAX} characters`) : null);

// ---------------------------------------------------------------------------------------------------- one action
type Send<T> = (key: string) => Promise<WriteResult<T>>;
/**
 * The busy state and the one-request rule every hook shares. `run` makes the action's key, checks the client-side
 * bounds, sends once and maps a failure to words; while one is out, another `run` sends nothing.
 */
function useAction() {
  const [state, setState] = useState<{ sending: boolean; error: WriteError | null }>({ sending: false, error: null });
  const inFlight = useRef(false);
  const run = useCallback(async <T>(send: Send<T>, options: { check?: () => WriteError | null; unitsOf?: (field: string | null) => FieldUnits } = {}): Promise<WriteOutcome<T>> => {
    if (inFlight.current) return { ok: false, error: null };
    const invalid = options.check?.() ?? null;
    if (invalid) {
      setState({ sending: false, error: invalid });
      return { ok: false, error: invalid };
    }
    inFlight.current = true;
    setState({ sending: true, error: null });
    try {
      const result = await send(newIdempotencyKey());
      setState({ sending: false, error: null });
      return { ok: true, data: result.data, replayed: result.replayed };
    } catch (caught) {
      const error = writeError(caught, options.unitsOf);
      setState({ sending: false, error });
      return { ok: false, error };
    } finally {
      inFlight.current = false;
    }
  }, []);
  const clearError = useCallback(() => setState((current) => (current.error ? { ...current, error: null } : current)), []);
  return { sending: state.sending, error: state.error, run, clearError };
}

/** Marks these reads stale and refetches the ones on screen, so a write shows its new state at once. */
function refresh(client: QueryClient, operationIds: readonly ReadOperationId[]) {
  for (const operationId of operationIds) void client.invalidateQueries({ queryKey: ["api", operationId] });
}
/** The newest callback, without re-creating the action when a screen passes a new function each render. */
function useLatest<T>(value: T) {
  const ref = useRef(value);
  useEffect(() => { ref.current = value; });
  return ref;
}

// ---------------------------------------------------------------------------------------------------- S settings
export type SettingInfo = Pick<Setting, "param" | "label" | "kind" | "unit" | "step" | "min" | "max" | "options">;
export type SettingSave = { readonly value: number; readonly manual?: boolean; readonly reason?: string | null };

/**
 * The Save toast: "Requested 1.55 kPa for VPD Mister Engage", the stored value with `Setting.unit` and then
 * `Setting.label`, plus "(snapped from 1.53)" when the server snapped it; a release reads "Released <label>: plan, band
 * or default applies". The value keeps the decimals it was stored with: Iris's 48.75 taken manual reads 48.75.
 */
export function settingToast(saved: Pick<DesiredSetting, "value" | "snapped_from">, setting: Pick<SettingInfo, "label" | "kind" | "unit" | "step" | "options">): string {
  if (saved.value === null) return `Released ${setting.label}: plan, band or default applies`;
  const snapped = saved.snapped_from !== null && saved.snapped_from !== undefined ? ` (snapped from ${formatNumber(saved.snapped_from)})` : "";
  return `Requested ${keptValueText(saved.value, setting)} for ${setting.label}${snapped}`;
}

/**
 * **S**: change or release one setting (API-10 `PUT /v1/settings/{param}`, API-11 `DELETE`), the three site caps
 * (`mister_daily_volume_max_gal`, `sched_mist_rh_max_pct`, `climate_yield_min`) included. Save always sends `manual`.
 * On a 200 it toasts, calls `onSaved` (the drawer closes) and refetches API-09, whose row then reads REQUESTED and is
 * read every 5 s (`useSettingsRead`) until the API says `confirmed`, `not_confirmed` or `held`; a site cap before the
 * irrigation firmware stays REQUESTED with `no_engine`. A refusal keeps the form open with `error` beside its field.
 */
export function useSettingWrite(setting: SettingInfo, { onSaved }: { onSaved?: (saved: DesiredSetting) => void } = {}) {
  const client = useQueryClient();
  const toast = useToast();
  const action = useAction();
  const latest = useLatest({ setting, onSaved });
  const { run } = action;
  const done = useCallback((outcome: WriteOutcome<DesiredSetting>) => {
    if (!outcome.ok) return outcome;
    toast.show({ message: settingToast(outcome.data, latest.current.setting) });
    latest.current.onSaved?.(outcome.data);
    refresh(client, ["API-09", "API-07", "API-17"]);
    return outcome;
  }, [client, toast, latest]);
  const unitsOf = useCallback((field: string | null): FieldUnits => (field === "value" ? { unit: latest.current.setting.unit, step: latest.current.setting.step } : {}), [latest]);
  const save = useCallback(async ({ value, manual = MANUAL_DEFAULT, reason }: SettingSave) => {
    const target = latest.current.setting;
    const check = () => {
      const range = outOfRange(value, target);
      return range ? { ...refused("value", range, "out_of_range"), min: target.min ?? null, max: target.max ?? null } : tooLong("reason", reason);
    };
    const body = { value, manual, ...(reason ? { reason } : {}) };
    return done(await run((key) => apiWrite("API-10", { param: target.param }, body, key), { check, unitsOf }));
  }, [run, done, unitsOf, latest]);
  const release = useCallback(async ({ reason }: { reason?: string | null } = {}) => {
    const target = latest.current.setting;
    const params = reason ? { param: target.param, reason } : { param: target.param };
    return done(await run((key) => apiWrite("API-11", params, undefined, key), { check: () => tooLong("reason", reason) }));
  }, [run, done, latest]);
  return { save, release, sending: action.sending, error: action.error, clearError: action.clearError };
}

// ---------------------------------------------------------------------------------------------------- P program
export type ProgramOperationId = "API-IRR-03" | "API-IRR-04" | "API-IRR-05" | "API-IRR-08";
/** A line edit that changes the line's definition stores the `manual` it carries (irrigation §8.3). */
export const LINE_DEFINITION_FIELDS = ["enabled", "vpd_member", "schedule_member", "daily_cap_min", "vpd_reserve_min"] as const;

/**
 * The body as the dashboard sends it: a schedule create or edit always carries `manual` (true unless the form
 * unticked it); a line edit that changes the definition (an enable, a mister switch, a cap) carries it too, and an inline
 * switch's true stands; a pause, a resume or a check alone carries none. A delete has no body.
 */
export function programBody<K extends ProgramOperationId>(operation: K, body: BodyOf<K>): BodyOf<K> {
  if (operation === "API-IRR-05" || body === undefined) return body;
  const input = body as Record<string, unknown>;
  if (operation === "API-IRR-08" && !LINE_DEFINITION_FIELDS.some((field) => input[field] !== undefined)) {
    const { manual: _manual, ...rest } = input;
    void _manual;
    return rest as BodyOf<K>;
  }
  return { ...input, manual: typeof input.manual === "boolean" ? input.manual : MANUAL_DEFAULT } as BodyOf<K>;
}

/**
 * **P**: create, edit or delete a schedule and change a line (API-IRR-03, -04, -05 and -08; the site caps use S). On
 * success it refetches API-IRR-01 and API-IRR-02, whose item then reads REQUESTED ("rev 43 sending",
 * `programSyncText`) and is read every 5 s until its `sync` is `confirmed` from `irr_program_id`, or "Not confirmed:
 * <reject>" after 5 min. Before the irrigation firmware (`engine: false`) it stays REQUESTED with "Applies with the
 * irrigation firmware". `unitsOf` gives a field's refusal its unit ("Must be 0–130 min").
 */
export function useProgramWrite<K extends ProgramOperationId>(operation: K, { onSaved, unitsOf }: { onSaved?: (data: Parsed<K>) => void; unitsOf?: (field: string | null) => FieldUnits } = {}) {
  const client = useQueryClient();
  const action = useAction();
  const latest = useLatest({ onSaved, unitsOf });
  const { run } = action;
  const send = useCallback(async (params: ParamsOf<K>, body: BodyOf<K>): Promise<WriteOutcome<Parsed<K>>> => {
    const input = (body ?? {}) as Record<string, unknown>;
    const check = () => tooLong("reason", typeof input.reason === "string" ? input.reason : null) ?? tooLong("check_note", typeof input.check_note === "string" ? input.check_note : null);
    const outcome = await run((key) => apiWrite(operation, params, programBody(operation, body), key), { check, unitsOf: latest.current.unitsOf });
    if (outcome.ok) {
      latest.current.onSaved?.(outcome.data);
      refresh(client, ["API-IRR-01", "API-IRR-02", "API-17"]);
    }
    return outcome;
  }, [operation, run, client, latest]);
  return { send, sending: action.sending, error: action.error, clearError: action.clearError };
}

/**
 * The words beside a program item's badge and on the program banner (screens §1.4 P): REQUESTED reads "rev 43 sending",
 * or "Applies with the irrigation firmware" before the engine; NOT CONFIRMED reads "Not confirmed: <reject in words>".
 */
export function programSyncText(sync: string, status: (Pick<IrrigationStatus, "engine"> & Partial<Pick<IrrigationStatus, "program">>) | null | undefined): string | null {
  if (sync === "requested") {
    if (status?.engine === false) return words("state_reason", "no_engine");
    const revision = status?.program?.revision;
    return revision !== null && revision !== undefined ? `rev ${revision} sending` : "Sending";
  }
  if (sync === "not_confirmed") {
    const program = status?.program;
    const reject = program?.reject_code ? words("program_reject", program.reject_code) : program?.reason || "no matching program on the controller";
    return `Not confirmed: ${reject}`;
  }
  return null;
}

// ---------------------------------------------------------------------------------------------------- readback
/** A read that answers a command, and what it must show. */
export type Readback<R extends ReadOperationId = ReadOperationId> = {
  readonly operationId: R;
  readonly params?: ParamsOf<R>;
  /** True once the read shows the expected state. */
  readonly done: (data: Parsed<R>) => boolean;
  /** Why it has not, in words, when the read says (a line's `state.reason`). */
  readonly reason?: (data: Parsed<R>) => string | null;
  /** True when the read already decides the outcome, so waiting longer cannot change it. */
  readonly decided?: (data: Parsed<R>) => boolean;
};
/** Builds a typed readback (the read's type flows into `done` and `reason`). */
export const readback = <R extends ReadOperationId>(check: Readback<R>): Readback<R> => check;

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(signal.reason); return; }
    const timer = setTimeout(() => { signal?.removeEventListener("abort", stop); resolve(); }, ms);
    function stop() { clearTimeout(timer); reject(signal?.reason); }
    signal?.addEventListener("abort", stop, { once: true });
  });
}
type Watched = { done: boolean; data: unknown };
/** Reads `check` every 5 s for up to 30 s (6 reads), stopping at the first that is done or decided. A failed read waits on. */
async function watch(client: QueryClient, check: Readback, signal?: AbortSignal): Promise<Watched> {
  let data: unknown;
  const loose = check as unknown as { operationId: ReadOperationId; params?: object; done: (data: unknown) => boolean; decided?: (data: unknown) => boolean };
  for (let waited = READBACK_POLL_MS; waited <= COMMAND_WINDOW_MS; waited += READBACK_POLL_MS) {
    await sleep(READBACK_POLL_MS, signal);
    let read: unknown;
    try {
      // The readback's own operation types its params; the loop only passes them through.
      read = await fetchRead(client, loose.operationId, (loose.params ?? {}) as never);
    } catch {
      continue;
    }
    data = read;
    if (loose.done(read)) return { done: true, data };
    if (loose.decided?.(read)) break;
  }
  return { done: false, data };
}

// ---------------------------------------------------------------------------------------------------- C commands
export type CommandOperationId = "API-IRR-06" | "API-IRR-07" | "API-IRR-12" | "API-IRR-13" | "API-20";
export type CommandPhase = "idle" | "sending" | "waiting" | "done" | "late" | "failed";
/** The words for a command whose readback has not changed within 30 s and gives no reason. */
export const NOT_STARTED = "Not started";

const lineIn = (status: IrrigationStatus, id: string): IrrigationLine | undefined => status.lines.find((line) => line.line_id === id);
const lineReason = (status: IrrigationStatus, ids: readonly string[]) => ids.map((id) => lineIn(status, id)?.state.reason).find((reason) => Boolean(reason)) ?? null;
/**
 * The default readback: API-IRR-01 and the line's relay edge, RUNNING after a run and OFF after a stop (every line for
 * a stop with no `line_id`); a cleared fault, the watchdog latch (`mister_fault`) gone from that line, or from every
 * line for a clear with no `line_id`, which clears every latch (the fertilizer misters L08 and L10 latch too,
 * irrigation F10; a back-off is no latch). Overrides (API-IRR-12) and lights (API-20) have none: their screens pass
 * API-02 `overrides` or API-13.
 */
export function defaultReadback(operation: CommandOperationId, params: Record<string, unknown>, body: Record<string, unknown>): Readback | null {
  const target = typeof params.line_id === "string" ? params.line_id : typeof body.line_id === "string" ? body.line_id : null;
  if (operation === "API-IRR-06" && target) {
    return readback({ operationId: "API-IRR-01", done: (status) => lineIn(status, target)?.state.code === "running", reason: (status) => lineReason(status, [target]) });
  }
  if (operation === "API-IRR-07") {
    const ids = (status: IrrigationStatus) => (target ? [target] : status.lines.map((line) => line.line_id));
    return readback({ operationId: "API-IRR-01", done: (status) => ids(status).every((id) => lineIn(status, id)?.state.code !== "running"), reason: (status) => lineReason(status, ids(status)) });
  }
  if (operation === "API-IRR-13") {
    const latched = (status: IrrigationStatus) => status.lines.filter((line) => (target === null || line.line_id === target) && line.fault === "mister_fault").map((line) => line.line_id);
    return readback({ operationId: "API-IRR-01", done: (status) => latched(status).length === 0, reason: (status) => lineReason(status, latched(status)) });
  }
  return null;
}

/** A command's body: Run, Stop, Clear fault and the bursts never carry `manual` (they never change it). */
export function commandBody<K extends CommandOperationId>(body: BodyOf<K>): BodyOf<K> {
  if (body === undefined || body === null || typeof body !== "object") return body;
  const { manual: _manual, ...rest } = body as Record<string, unknown>;
  void _manual;
  return rest as BodyOf<K>;
}

/**
 * **C**: one command (API-IRR-06 run, -07 stop, -12 burst, -13 clear fault; API-20 for the grow lights). "Sending…"
 * until the answer (a 202 `Command`, or API-20's 200), when `send` resolves (a dialog closes on it); then the readback
 * every 5 s until it shows the expected state (`phase` done). If nothing has changed within 30 s, `phase` is late and
 * `message` the line's reason in words, or "Not started". A refusal keeps the dialog open with `error` beside its
 * field. Pass `readback` for another read and test (API-02 `overrides` for a burst); a function gets the request's
 * params and body.
 */
export function useCommand<K extends CommandOperationId>({ operation, readback: chosen }: {
  operation: K;
  readback?: Readback | null | ((params: ParamsOf<K>, body: BodyOf<K>) => Readback | null);
}) {
  const client = useQueryClient();
  const action = useAction();
  const [progress, setProgress] = useState<{ phase: Exclude<CommandPhase, "sending" | "failed">; message: string | null }>({ phase: "idle", message: null });
  const latest = useLatest(chosen);
  const watching = useRef<AbortController | null>(null);
  useEffect(() => () => watching.current?.abort(), []);
  const { run } = action;
  const send = useCallback(async (params: ParamsOf<K>, body: BodyOf<K>): Promise<WriteOutcome<Parsed<K>>> => {
    const outcome = await run((key) => apiWrite(operation, params, commandBody<K>(body), key));
    if (!outcome.ok) return outcome;
    const option = latest.current;
    const check = option === undefined
      ? defaultReadback(operation, params as Record<string, unknown>, (body ?? {}) as Record<string, unknown>)
      : typeof option === "function" ? option(params, body) : option;
    watching.current?.abort();
    if (!check) {
      setProgress({ phase: "done", message: null });
      return outcome;
    }
    const controller = new AbortController();
    watching.current = controller;
    setProgress({ phase: "waiting", message: null });
    void watch(client, check, controller.signal).then((seen) => {
      if (controller.signal.aborted) return;
      if (seen.done) { setProgress({ phase: "done", message: null }); return; }
      const reason = seen.data === undefined ? null : (check.reason as ((data: unknown) => string | null) | undefined)?.(seen.data) ?? null;
      setProgress({ phase: "late", message: reason || NOT_STARTED });
    }, () => {
      // Unmounted or replaced by a newer command: nothing to show.
    });
    return outcome;
  }, [operation, run, client, latest]);
  const phase: CommandPhase = action.sending ? "sending" : action.error ? "failed" : progress.phase;
  return { send, phase, sending: action.sending, message: progress.message, error: action.error, clearError: action.clearError };
}

// ---------------------------------------------------------------------------------------------------- R records
/** Replaces one alert in every cached API-14 page, so its row reads acknowledged or resolved at once. */
function storeAlert(client: QueryClient, updated: Alert) {
  client.setQueriesData<AlertPage>({ queryKey: ["api", "API-14"] }, (page) => (page ? { ...page, items: page.items.map((alert) => (alert.id === updated.id ? updated : alert)) } : page));
}

/**
 * **R**: acknowledge (API-15, optional `note`) or resolve (API-16, optional `resolution`) an alert, each at most 500
 * characters. Saved at once: the returned `Alert` replaces the row in every cached alert list ("Acknowledged by jason ·
 * 06:47"), then the lists, Now's alert count and Activity refetch.
 */
export function useRecordWrite() {
  const client = useQueryClient();
  const action = useAction();
  const { run } = action;
  const done = useCallback((outcome: WriteOutcome<Alert>) => {
    if (outcome.ok) {
      storeAlert(client, outcome.data);
      refresh(client, ["API-14", "API-02", "API-17"]);
    }
    return outcome;
  }, [client]);
  const acknowledge = useCallback(async (id: number, note?: string | null) => {
    return done(await run((key) => apiWrite("API-15", { id }, note ? { note } : undefined, key), { check: () => tooLong("note", note) }));
  }, [run, done]);
  const resolve = useCallback(async (id: number, resolution?: string | null) => {
    return done(await run((key) => apiWrite("API-16", { id }, resolution ? { resolution } : undefined, key), { check: () => tooLong("resolution", resolution) }));
  }, [run, done]);
  return { acknowledge, resolve, sending: action.sending, error: action.error, clearError: action.clearError };
}

// ---------------------------------------------------------------------------------------------------- Stop
/** Lines a Stop result offers to disable: the ones with a card and an enable, never L03 (the fert master) or L11 (fog). */
const DISABLEABLE = /^L(0[124-9]|10)$/;
const heartbeatStale = (ingestor: Freshness) => ingestor.stale || ingestor.age_s === null || ingestor.age_s > HEARTBEAT_FRESH_S;
const controllerReporting = (climate: Freshness) => climate.age_s !== null && climate.age_s <= CLIMATE_REPORTING_S;
/** The wet lines in API-02 `running` (the entries with a `line_id`), within the Stop's scope. */
export function runningLines(now: Pick<Now, "running"> | null | undefined, scope: string | null = null): string[] {
  const ids = (now?.running ?? []).map((entry) => entry.line_id).filter((id): id is string => typeof id === "string" && id.length > 0);
  return [...new Set(ids)].filter((id) => scope === null || id === scope);
}

/**
 * The newest API-02 answer the page holds: the shell's own poll (`NOW_QUERY_KEY`, on every page) or a screen's
 * `useNowRead()` entry, whichever answered last; undefined before either has.
 */
export function newestNow(client: QueryClient): Now | undefined {
  const held = [client.getQueryState<Now>(NOW_QUERY_KEY), client.getQueryState<Now>(readKey("API-02"))]
    .filter((state) => state?.data !== undefined)
    .sort((a, b) => (b?.dataUpdatedAt ?? 0) - (a?.dataUpdatedAt ?? 0));
  return held[0]?.data;
}

export type StopReport = {
  /** The wet lines running before the Stop, from API-02. */
  readonly running: readonly string[];
  /** API-IRR-01's engine flag before the Stop: null while that read fails or has not answered. */
  readonly engine: boolean | null;
  /** Every line that was running left API-02 `running` (its OFF edge) within 30 s. */
  readonly confirmed: boolean;
  /** API-02 `freshness` from the newest read after the 202; null when none answered. */
  readonly freshness: Pick<Now["freshness"], "ingestor" | "climate"> | null;
};
export type StopResult = { readonly kind: "success" | "failure"; readonly message: string; readonly detail: string; readonly disable: readonly string[] };

/**
 * The one Stop result (screens §1.4), the first that holds: the ingestor not running; the controller not reporting or
 * a running line not OFF within 30 s; no irrigation firmware; the firmware state unknown; else stopped, with Disable for
 * each line that was running (a disable applies only with the irrigation firmware, so it is offered only then).
 */
export function stopResult({ running, engine, confirmed, freshness }: StopReport): StopResult {
  if (freshness && heartbeatStale(freshness.ingestor)) {
    const at = freshness.ingestor.observed_at;
    const since = at && Number.isFinite(Date.parse(at)) ? ` since ${shortTime(at)}` : "";
    return { kind: "failure", message: `Stop saved but not sent: ingestor not running${since}.`, detail: "Running drips end on their own timer; close the spigot if water must stop now.", disable: [] };
  }
  if (!confirmed || !freshness || !controllerReporting(freshness.climate)) {
    return { kind: "failure", message: "The controller hasn't confirmed the stop.", detail: "Close the spigot to stop water now.", disable: [] };
  }
  const message = "Stopped every running valve.";
  if (engine === false) return { kind: "success", message, detail: "VPD misting and fog can still start on their own.", disable: [] };
  if (engine === null) return { kind: "success", message, detail: "VPD misting, fog and the next scheduled run can still start on their own.", disable: [] };
  return {
    kind: "success", message, detail: "VPD misting, fog and the next scheduled run can still start on their own; to keep a line off, disable it.",
    disable: running.filter((id) => DISABLEABLE.test(id)),
  };
}

/** Disable for one line in the Stop result: API-IRR-08 `{enabled: false, manual: true}` through the P hook. */
function DisableLine({ lineId }: { lineId: string }) {
  const write = useProgramWrite("API-IRR-08");
  const [sent, setSent] = useState(false);
  if (sent) return createElement("span", { className: "state-note", "data-disabled-line": lineId }, `${lineId}: disable requested`);
  const press = async () => { if ((await write.send({ line_id: lineId }, { enabled: false, manual: true })).ok) setSent(true); };
  return createElement(Fragment, null,
    createElement(Button, { write: true, busy: write.sending, onClick: () => void press(), children: `Disable ${lineId}` }),
    write.error && createElement("span", { className: "field-error" }, `${lineId}: ${write.error.message}`));
}
function DisableLines({ lines }: { lines: readonly string[] }) {
  return createElement(Fragment, null, ...lines.map((lineId) => createElement(DisableLine, { key: lineId, lineId })));
}
/** The Stop result as a toast that stays until dismissed (design system §4.5). */
export function stopToast(result: StopResult): ToastOptions {
  return {
    kind: result.kind, message: result.message, detail: result.detail, sticky: true,
    actions: result.disable.length ? createElement(DisableLines, { lines: result.disable }) : undefined,
  };
}

/**
 * Stop one line (`stop("L01")`) or every line (`stop()`): `POST /api/v1/irrigation/stop` (API-IRR-07) with
 * `{line_id}` or `{}`, one tap, no dialog. It is never disabled by the writes flag: the BFF never blocks it, and before
 * the irrigation firmware the server turns every open wet relay off through the run-now bridge.
 *
 * Before sending it notes the wet lines running in the newest API-02 answer the page holds (`newestNow`: the shell's
 * poll, or a screen's own read) and the `engine` flag of the shared API-IRR-01 entry (unknown while that read fails or
 * is not served). `stop` resolves at the 202; then it reads API-02 every 5 s for up to 30 s until each of those lines
 * has left `running`, takes `freshness` from the same reads, and shows one result toast that stays until dismissed
 * (`stopResult`). With no API-02 data before the Stop, the first read after it says what must go OFF. The check runs on
 * after the screen that pressed Stop goes away, since the toast belongs to the app; a newer Stop from the same control
 * replaces it.
 */
export function useStop() {
  const client = useQueryClient();
  const toast = useToast();
  const [phase, setPhase] = useState<"idle" | "sending" | "confirming">("idle");
  const inFlight = useRef(false);
  const generation = useRef(0);
  const stop = useCallback(async (lineId: string | null = null): Promise<WriteOutcome<Parsed<"API-IRR-07">>> => {
    if (inFlight.current) return { ok: false, error: null };
    inFlight.current = true;
    const run = ++generation.current;
    const before = newestNow(client);
    let running: string[] | null = before ? runningLines(before, lineId) : null;
    const engine = engineFlag(client);
    setPhase("sending");
    let accepted: WriteResult<Parsed<"API-IRR-07">>;
    try {
      accepted = await apiWrite("API-IRR-07", {}, lineId ? { line_id: lineId } : {}, newIdempotencyKey());
    } catch (caught) {
      const error = writeError(caught);
      setPhase("idle");
      toast.show({ kind: "failure", message: `Stop not sent: ${error.message.replace(/\.$/, "")}.`, detail: "Close the spigot to stop water now." });
      return { ok: false, error };
    } finally {
      inFlight.current = false;
    }
    setPhase("confirming");
    const left = (now: Now) => {
      const still = runningLines(now, lineId);
      running ??= still;
      return running.every((id) => !still.includes(id));
    };
    const decided = (now: Now) => heartbeatStale(now.freshness.ingestor) || !controllerReporting(now.freshness.climate);
    const report = (seen: Watched) => {
      if (generation.current !== run) return;
      setPhase("idle");
      const last = seen.data as Now | undefined;
      toast.show(stopToast(stopResult({ running: running ?? [], engine, confirmed: seen.done, freshness: last ? last.freshness : null })));
    };
    // A check that fails outright confirms nothing: the result then says to close the spigot.
    void watch(client, readback({ operationId: "API-02", done: left, decided })).then(report, () => report({ done: false, data: undefined }));
    return { ok: true, data: accepted.data, replayed: accepted.replayed };
  }, [client, toast]);
  return { stop, sending: phase === "sending", confirming: phase === "confirming" };
}

// SPDX-License-Identifier: MIT
/**
 * The shared reads (architecture §3). Each read has one TanStack Query key, so the shell, every screen and the write
 * hooks share one cache entry and one poll: a write hook reads the entry before it sends (Stop notes what is running
 * from the shell's API-02 and the engine flag from API-IRR-01) and refetches it after (a Save shows REQUESTED at once).
 * Screens and the shell read through these hooks, never under a key of their own.
 *
 * Cadence: API-02, API-13 and API-IRR-01 every 15 s, settings, schedules and the equipment lanes every 60 s, and every
 * 5 s while a write in them is still settling (a REQUESTED setting or program item). An item that waits for the
 * irrigation firmware cannot settle before it ships, so it keeps the slow cadence.
 */
import { useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { useServerSkew } from "../components/freshness";
import { apiGet, type IrrigationStatus, type ParamsOf, type Parsed, type ReadOperationId, type ScheduleAgenda, type Settings, type StateLanes } from "./operator-api";
import { POLL_TIMEOUT_MS, usePolledQuery } from "./use-current";

/** The one key for a read: `["api", operationId]`, plus the params when there are any. */
export function readKey<K extends ReadOperationId>(operationId: K, params?: ParamsOf<K>): QueryKey {
  return params && Object.keys(params).length > 0 ? ["api", operationId, params] : ["api", operationId];
}

/** Base cadences (architecture §3). */
export const READ_INTERVAL_MS = { now: 15_000, irrigation: 15_000, equipment: 15_000, settings: 60_000, schedules: 60_000, states: 60_000 } as const;
/** A setting or irrigation write still REQUESTED, or a command still pending, is read every 5 s until it settles. */
export const SETTLING_INTERVAL_MS = 5_000;

export type ReadOptions<K extends ReadOperationId> = {
  intervalMs: number;
  /** True while the newest data holds a write that is still settling: the read then polls every 5 s. */
  settling?: (data: Parsed<K>) => boolean;
};

/** Polls one read under its shared key (`usePolledQuery`: paused while hidden, backing off on failures). */
export function useApiRead<K extends ReadOperationId>(operationId: K, params: ParamsOf<K>, { intervalMs, settling }: ReadOptions<K>) {
  const client = useQueryClient();
  const key = readKey(operationId, params);
  const cached = client.getQueryData<Parsed<K>>(key);
  const interval = cached !== undefined && settling?.(cached) ? Math.min(SETTLING_INTERVAL_MS, intervalMs) : intervalMs;
  return usePolledQuery<Parsed<K>>(key, (signal) => apiGet(operationId, params, { signal }), interval);
}

/** One read now, into the shared cache entry, so every view of it updates. It gives up after the poll timeout. */
export function fetchRead<K extends ReadOperationId>(client: QueryClient, operationId: K, params: ParamsOf<K>): Promise<Parsed<K>> {
  return client.fetchQuery({
    queryKey: readKey(operationId, params), staleTime: 0, retry: false,
    // A timeout of its own, not TanStack's signal: an observer leaving must not cancel a readback in progress.
    queryFn: () => apiGet(operationId, params, { signal: AbortSignal.timeout(POLL_TIMEOUT_MS) }),
  });
}

/**
 * The irrigation firmware flag from the shared API-IRR-01 entry: true or false from its newest successful read, null
 * (unknown) while that read fails or has not answered, as at R1 before API-IRR-01 is served.
 */
export function engineFlag(client: QueryClient): boolean | null {
  const state = client.getQueryState<IrrigationStatus>(readKey("API-IRR-01"));
  return state?.status === "success" && state.data ? state.data.engine : null;
}

/**
 * A setting write still settling: its row reads REQUESTED. A row with no write of its own, or one that waits for the
 * irrigation firmware (`no_engine`, a site cap before the engine), is not settling.
 */
export function settingsSettling(settings: Settings): boolean {
  return settings.items.some((row) => row.state === "requested" && row.desired !== null && row.desired.state === "requested" && row.desired.state_reason?.code !== "no_engine");
}
/** A program item still settling: with the engine on the controller, the program or a line reads REQUESTED. */
export function irrigationSettling(status: IrrigationStatus): boolean {
  return status.engine && (status.program.state === "requested" || status.lines.some((line) => line.sync === "requested"));
}
/** A schedule still settling: one reads REQUESTED, unless the engine is known to be missing (`engine` false). */
export function schedulesSettling(agenda: ScheduleAgenda, engine: boolean | null): boolean {
  return engine !== false && agenda.schedules.some((schedule) => schedule.sync === "requested");
}

/** API-02, the shell's read on every page: the fault banner, `running` and `freshness` (15 s). */
export function useNowRead() {
  return useApiRead("API-02", {}, { intervalMs: READ_INTERVAL_MS.now });
}
/** API-IRR-01, the one water status read that Now's water card and Water share (15 s; 5 s while an item settles). */
export function useIrrigationRead() {
  return useApiRead("API-IRR-01", {}, { intervalMs: READ_INTERVAL_MS.irrigation, settling: irrigationSettling });
}
/** API-09, the settings catalog (60 s; 5 s while a row reads REQUESTED). */
export function useSettingsRead() {
  return useApiRead("API-09", {}, { intervalMs: READ_INTERVAL_MS.settings, settling: settingsSettling });
}
/** API-IRR-02, schedules and the week (60 s; 5 s while a schedule reads REQUESTED and the engine is not known missing). */
export function useSchedulesRead(params: ParamsOf<"API-IRR-02"> = {}) {
  const client = useQueryClient();
  return useApiRead("API-IRR-02", params, { intervalMs: READ_INTERVAL_MS.schedules, settling: (agenda) => schedulesSettling(agenda, engineFlag(client)) });
}
/** API-13, every actuator's state now with since, runtime and cycles today (15 s): the Equipment board, and Now. */
export function useEquipmentRead() {
  return useApiRead("API-13", {}, { intervalMs: READ_INTERVAL_MS.equipment });
}

/** The one key for the API-05 window of the last `seconds`: a poll moves the window, never the key. */
export const statesWindowKey = (seconds: number): QueryKey => ["api", "API-05", { last_s: seconds }];
/**
 * API-05 over the last `seconds` (60 s). Each poll asks for the window that ends at its own moment on the server's
 * clock (API-02's skew), so the lanes slide with time under one cache entry, and a device clock running ahead or behind
 * never leaves an empty edge that would read as off. The answer's own `from` and `to` are the window to draw.
 */
export function useStatesWindowRead(seconds: number) {
  const skewMs = useServerSkew();
  return usePolledQuery<StateLanes>(statesWindowKey(seconds), (signal) => {
    const to = Date.now() - skewMs;
    return apiGet("API-05", { from: new Date(to - seconds * 1000).toISOString(), to: new Date(to).toISOString() }, { signal });
  }, READ_INTERVAL_MS.states);
}

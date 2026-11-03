// SPDX-License-Identifier: MIT
/**
 * The bursts and Restart controller on Equipment (SCR-07, US-EQP-06), which replace the Home Assistant buttons; nothing
 * here calls Home Assistant. Each is one API-IRR-12 through the C hook, with no dialog: "Sending…" until the 202, then
 * API-02 every 5 s. What runs is only ever the controller's readback, API-02 `overrides`, never the command. Operators
 * press; viewers read what runs. With changes switched off the buttons are disabled (`Button write`), and before the
 * irrigation firmware API-IRR-12 answers 503, so the panel says when they arrive instead. The engine flag is API-IRR-01's
 * alone: until it answers nothing is offered.
 */
import { useId, useRef, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { leftWords } from "../lib/format";
import type { BodyOf, Now, ParamsOf } from "../lib/operator-api";
import { READ_INTERVAL_MS, panelRead, useApiRead } from "../lib/queries";
import { OperatorOnly } from "../lib/session";
import { useShellNow, useShellStatus } from "../lib/shell";
import { words } from "../lib/words";
import { newestNow, readback, useCommand, type CommandPhase, type Readback } from "../lib/writes";
import { Button } from "./button";
import { Details } from "./details";
import { useNow } from "./freshness";
import { PanelError } from "./panel";

/** The three bursts as the Home Assistant buttons ran them. */
export const BURSTS = [
  { kind: "fans", minutes: 15, label: "Fans 15 min" },
  { kind: "humidify", minutes: 5, label: "Humidify 5 min" },
  { kind: "vent_bypass", minutes: 10, label: "Vent bypass 10 min" },
] as const;
export const BURSTS_ARRIVE_TEXT = "Bursts arrive with the irrigation firmware";
export const RESTART_ARRIVES_TEXT = "Restart arrives with the irrigation firmware";
export const NO_BURSTS_TEXT = "Running: none";
export const RESTART_SENT_TEXT = "Restart sent";
/** The controller's uptime resets within this long of a restart (SCR-07). */
export const RESTART_WINDOW_MS = 180_000;

/** The modes the controller ignores bursts in (`manual_override` yields to safety). */
const BURSTS_IGNORED: Readonly<Record<string, string>> = { SAFETY_COOL: "emergency cooling", SAFETY_HEAT: "emergency heating", SENSOR_FAULT: "a sensor fault" };
/** "Bursts are ignored during emergency cooling" while API-02 `mode.code` is one of them; null otherwise. */
export function burstsIgnoredText(mode: string | null | undefined): string | null {
  const during = mode ? BURSTS_IGNORED[mode] : undefined;
  return during ? `Bursts are ignored during ${during}` : null;
}

const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);
/**
 * One running burst in words: "Fans burst · 12 min left" from `ends_at`, no time left for a push-button burst (`ends_at`
 * null), or why the controller holds it off: "Humidify held off: someone inside".
 */
export function burstWords(burst: Now["overrides"][number], clock: number): string {
  const name = words("burst", burst.kind);
  if (burst.held_reason) return `${name.replace(/ burst$/, "")} held off: ${lowerFirst(words("fog_block_reason", burst.held_reason))}`;
  const left = leftWords(burst.ends_at, clock);
  return left ? `${name} · ${left}` : name;
}

/** A burst's readback is its flag on in API-02 `overrides`; Clear's is none left on. A restart passes its own. */
function overrideReadback(_params: ParamsOf<"API-IRR-12">, body: BodyOf<"API-IRR-12">): Readback | null {
  if (body.kind === "clear") return readback({ operationId: "API-02", done: (now) => now.overrides.length === 0, reason: () => "The controller still reports a burst" });
  return readback({ operationId: "API-02", done: (now) => now.overrides.some((burst) => burst.kind === body.kind) });
}

/** What a command says after its 202: waiting on the readback, or within its window why nothing changed. */
function CommandNote({ phase, message, error }: { phase: CommandPhase; message: string | null; error: { readonly message: string } | null }) {
  return <>
    {phase === "waiting" && <span className="state-note">Waiting for the controller</span>}
    {phase === "late" && <span className="state-note">{message}</span>}
    {error && <span className="field-error" role="alert">{error.message}</span>}
  </>;
}

function BurstButton({ kind, minutes, children }: { kind: (typeof BURSTS)[number]["kind"]; minutes: number; children: ReactNode }) {
  const command = useCommand({ operation: "API-IRR-12", readback: overrideReadback });
  return <>
    <Button write wide busy={command.sending} onClick={() => void command.send({}, { kind, minutes })}>{children}</Button>
    <CommandNote phase={command.phase} message={command.message} error={command.error} />
  </>;
}

/**
 * Clear: API-IRR-12 `{kind: "clear"}`, which ends every burst, so it stands once however many run. Equipment's panel and
 * Now's burst chip both use it.
 */
export function ClearBursts() {
  const command = useCommand({ operation: "API-IRR-12", readback: overrideReadback });
  return <>
    <Button write wide busy={command.sending} onClick={() => void command.send({}, { kind: "clear" })}>Clear <span className="screen-reader">bursts</span></Button>
    <CommandNote phase={command.phase} message={command.message} error={command.error} />
  </>;
}

/**
 * Restart controller: API-IRR-12 `{kind: "restart"}`. It reads "Restart sent" until API-02 `controller.uptime_s` drops
 * below the value noted before the press, within 3 min; with none noted, until it reads under those 3 min.
 */
function RestartController() {
  const client = useQueryClient();
  const before = useRef<number | null>(null);
  const check = () => readback({
    operationId: "API-02", windowMs: RESTART_WINDOW_MS, reason: () => "The controller has not restarted",
    done: (now) => {
      const uptime = now.controller?.uptime_s;
      if (uptime === null || uptime === undefined) return false;
      return uptime < (before.current ?? RESTART_WINDOW_MS / 1000);
    },
  });
  const command = useCommand({ operation: "API-IRR-12", readback: check });
  const press = () => {
    before.current = newestNow(client)?.controller?.uptime_s ?? null;
    void command.send({}, { kind: "restart" });
  };
  const waiting = command.phase === "waiting";
  return <div className="actions burst-actions">
    <Button write wide busy={command.sending} disabled={waiting} onClick={press}>{waiting ? RESTART_SENT_TEXT : "Restart controller"}</Button>
    {command.phase === "done" && <span className="state-note">The controller restarted</span>}
    {command.phase === "late" && <span className="state-note">{command.message}</span>}
    {command.error && <span className="field-error" role="alert">{command.error.message}</span>}
  </div>;
}

function BurstControls({ engine, failed, retry }: { engine: boolean | null; failed: boolean; retry: () => void }) {
  if (failed) return <PanelError thing="bursts" text="Can't tell whether the controller has the irrigation firmware yet." onRetry={retry} />;
  if (engine === null) return <><div className="skeleton skeleton-sm" aria-hidden="true" /><p className="screen-reader">Loading the bursts</p></>;
  if (!engine) {
    return <>
      <p className="state-note" data-arrives="bursts">{BURSTS_ARRIVE_TEXT} (<code>manual_override</code>)</p>
      <p className="state-note" data-arrives="restart">{RESTART_ARRIVES_TEXT}</p>
    </>;
  }
  return <>
    <div className="actions burst-actions">
      {BURSTS.map((burst) => <BurstButton key={burst.kind} kind={burst.kind} minutes={burst.minutes}>{burst.label}</BurstButton>)}
      <ClearBursts />
    </div>
    <Details summary="More actions" className="burst-more"><RestartController /></Details>
  </>;
}

/** What runs, from the controller's readback alone: unknown while the page cannot vouch for it, never "none". */
function RunningBursts() {
  const shell = useShellNow();
  const status = useShellStatus();
  const clock = useNow();
  const now = shell.data;
  if (shell.pending && !now) return <><div className="skeleton skeleton-chips" aria-hidden="true" /><p className="screen-reader">Loading what is running</p></>;
  const reportsNot = status.ingestorRunning === false || status.controllerReporting === false;
  if (!now || shell.error || reportsNot) return <p className="state-note" data-bursts="unknown">Running: unknown</p>;
  const ignored = burstsIgnoredText(now.mode.code);
  return <>
    {now.overrides.length === 0 ? <p className="state-note" data-bursts="none">{NO_BURSTS_TEXT}</p> : <div className="burst-running" data-bursts="running">
      <span className="state-note">Running:</span>
      <ul className="chips">{now.overrides.map((burst) => <li key={burst.kind}><span className="chip chip-static" data-chip="burst">{burstWords(burst, clock)}</span></li>)}</ul>
    </div>}
    {ignored && <p className="fault" data-bursts="ignored">{ignored}</p>}
  </>;
}

export function BurstPanel() {
  const headingId = useId();
  // The engine flag changes only with an OTA, so this reads the shared API-IRR-01 entry at the slow cadence.
  const irrigation = panelRead(useApiRead("API-IRR-01", {}, { intervalMs: READ_INTERVAL_MS.settings }));
  return <section className="card burst-panel" aria-labelledby={headingId}>
    <h2 id={headingId} className="eyebrow">Bursts</h2>
    <OperatorOnly><BurstControls engine={irrigation.failed ? null : irrigation.data?.engine ?? null} failed={irrigation.failed} retry={irrigation.retry} /></OperatorOnly>
    <RunningBursts />
  </section>;
}

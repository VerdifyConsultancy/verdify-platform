// SPDX-License-Identifier: MIT
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import type { Equipment, ParamsOf, Setting } from "../../lib/operator-api";
import { fetchRead } from "../../lib/queries";
import { OperatorOnly } from "../../lib/session";
import { shortTime } from "../../lib/format";
import { words } from "../../lib/words";
import { readback, useCommand, useSettingWrite, type SettingInfo, type WriteError } from "../../lib/writes";
import { ReadbackBadge } from "../badge";
import { Button } from "../button";
import { StateReasonLine } from "../state-reason-line";

export type LightId = ParamsOf<"API-20">["light"];
export type LightState = ParamsOf<"API-20">["state"];
type LightItem = Equipment["items"][number];

/** The two grow lights: API-20's path, the board's `equipment_state` key (API-20's Activity target) and the automatic switch. */
export const LIGHTS: readonly { readonly light: LightId; readonly equipment: string; readonly autoParam: string }[] = [
  { light: "main", equipment: "grow_light_main", autoParam: "sw_gl_main_auto_mode" },
  { light: "grow", equipment: "grow_light_grow", autoParam: "sw_gl_grow_auto_mode" },
];

/** The reason a Back to automatic write and its resync carry into Activity. */
export const BACK_TO_AUTOMATIC = "Back to automatic";
/**
 * How long a Turn on or Turn off lasts (#1335): API-20's default `minutes`, like the Fans burst. Then the operator API
 * sends the controller's intent, so the light is back on automatic; the page sends no minutes of its own.
 */
export const MANUAL_MINUTES = 15;

const known = (state: string | null | undefined): state is LightState => state === "on" || state === "off";
/** The controller's intent and the Lutron readback are both known and say different things. */
export function lightsDiffer(intent: string | null | undefined, state: string | null | undefined): boolean {
  return known(intent) && known(state) && intent !== state;
}
/** The one API-20 state Back to automatic sends: the intent's, when the intent and Lutron are both known and differ; else none. */
export function resyncState(item: Pick<LightItem, "intent" | "state"> | null | undefined): LightState | null {
  return item && known(item.intent) && lightsDiffer(item.intent, item.state) ? item.intent : null;
}
/**
 * When a Turn on or Turn off still running goes back to automatic (#1377's timer, API-13 `manual_until`), or null. The
 * API names it only while it is ahead; once the controller's intent and Lutron read the same the switch no longer holds
 * anything (a decision change brought automatic back early, or Back to automatic matched them), so it is not shown.
 */
export function manualUntil(item: Pick<LightItem, "state" | "intent" | "manual_until"> | null | undefined): string | null {
  const until = item?.manual_until ?? null;
  if (until === null || (known(item?.intent) && known(item?.state) && item.intent === item.state)) return null;
  return until;
}
/** "by hand until 10:57": a timed switch's end in words. */
export const manualUntilWords = (until: string): string => `by hand until ${shortTime(until)}`;

const itemIn = (answer: Pick<Equipment, "items"> | null | undefined, equipment: string) => answer?.items.find((item) => item.equipment === equipment);
/** `unavailable`, API-20's own 503 with its `Problem.result` or no answer reaching the page: the light may still switch. */
const mayStillSwitch = (error: WriteError) => error.code === "unavailable";
/** What the row asks of API-13 after an API-20 command: Lutron reads the requested state. */
const lutronReads = (equipment: string, state: LightState) => readback({ operationId: "API-13", done: (answer) => itemIn(answer, equipment)?.state === state });

/**
 * "Lutron hasn't confirmed: still OFF", in the newest readback's words: the 30 s are up, and the board's 15 s poll goes on
 * showing the truth.
 */
export function unconfirmedWords(state: string | null | undefined): string {
  if (state === "on" || state === "off") return `Lutron hasn't confirmed: still ${state.toUpperCase()}`;
  return "Lutron hasn't confirmed: its state is unknown";
}

/**
 * One light's controls, held above the board so a failed API-13 read, which replaces the three groups with its error,
 * loses no answer and no resync. Turn on and Turn off are API-20 with API-13's Lutron state as the readback (5 s, 30 s at
 * most), through a 503 too because the light may switch late. Back to automatic is one API-10 write of the automatic
 * switch; once API-09 reads that write CONFIRMED it reads API-13 again and sends one API-20 command when `resyncState`
 * names one, because the controller switches a light only when its own decision changes. A later Turn on or Turn off
 * supersedes a resync still waiting, and a newer write of the switch takes its place.
 */
export function useLightControl({ light, equipment, autoParam }: (typeof LIGHTS)[number], setting: Setting | undefined) {
  const client = useQueryClient();
  const command = useCommand({ operation: "API-20", readback: (params: ParamsOf<"API-20">) => lutronReads(equipment, params.state), readbackAfter: mayStillSwitch });
  // Before API-09 answers, the write can still name its setting; only the toast reads the difference.
  const info: SettingInfo = setting ?? { param: autoParam, label: autoParam, kind: "switch", unit: null, step: null, min: null, max: null };
  const write = useSettingWrite(info);
  /** Which button pressed the command in flight or being read back. */
  const [pressed, setPressed] = useState<LightState | "automatic" | null>(null);
  /** The write this control made, and whether its resync is still to come. */
  const [automatic, setAutomatic] = useState<{ readonly writeId: number; readonly resync: "waiting" | "done" | "unread" } | null>(null);
  // Counts the presses, so a resync reading API-13 stands down if a newer press came meanwhile.
  const presses = useRef(0);

  // A 503 is logged too, so Activity reads again either way.
  const switchTo = async (state: LightState, body: { reason: string } | undefined) => {
    await command.send({ light, state }, body);
    void client.invalidateQueries({ queryKey: ["api", "API-17"] });
  };
  const turn = (state: LightState) => {
    presses.current += 1;
    setPressed(state);
    write.clearError();
    setAutomatic((current) => (current && current.resync !== "done" ? { ...current, resync: "done" } : current));
    void switchTo(state, undefined);
  };
  const backToAutomatic = async () => {
    presses.current += 1;
    setPressed("automatic");
    // What the last command said and is still waiting for no longer stands: this press takes its place.
    command.reset();
    setAutomatic((current) => (current?.resync === "unread" ? { ...current, resync: "done" } : current));
    // Not manual (#1335): a light on Auto is the controller's, so it leaves Settings' manual count.
    const outcome = await write.save({ value: 1, manual: false, reason: BACK_TO_AUTOMATIC });
    if (outcome.ok) setAutomatic({ writeId: outcome.data.id, resync: "waiting" });
  };

  const desired = setting?.desired ?? null;
  const confirmed = automatic?.resync === "waiting" && desired?.id === automatic.writeId && setting?.state === "confirmed";
  const superseded = automatic?.resync === "waiting" && desired !== null && desired.id > automatic.writeId;
  useEffect(() => {
    if (superseded) setAutomatic(null);
    if (!confirmed) return;
    setAutomatic((current) => (current ? { ...current, resync: "done" } : current));
    const press = presses.current;
    fetchRead(client, "API-13", {}).then((answer) => {
      const state = resyncState(itemIn(answer, equipment));
      if (state === null || press !== presses.current) return;
      setPressed("automatic");
      void switchTo(state, { reason: BACK_TO_AUTOMATIC });
    }, () => {
      // Not known, so none is sent; the board's own read shows why, and the row says the light was not matched.
      if (press === presses.current) setAutomatic((current) => (current ? { ...current, resync: "unread" } : current));
    });
    // One read and at most one command per CONFIRMED: `confirmed` falls with the state set above.
  }, [confirmed, superseded]);

  return {
    equipment, autoParam, setting, turn, backToAutomatic, pressed,
    command: { phase: command.phase, sending: command.sending, error: command.error },
    write: { sending: write.sending, error: write.error },
    /** This control's own write: CONFIRMED reads on the row for as long as it stands. */
    writeId: automatic?.writeId ?? null,
    /** The write has been sent and API-09 has neither confirmed nor refused it yet. */
    awaitingConfirm: automatic?.resync === "waiting" && setting?.state !== "not_confirmed" && setting?.state !== "held",
    unread: automatic?.resync === "unread",
  };
}
export type LightControl = ReturnType<typeof useLightControl>;

/**
 * A light's buttons by state (redesign §4.5: one primary, chosen by state). In automatic and matching the controller's
 * intent, only the switch to the other state: "Turn on" while off, "Turn off" while on. Overridden by hand (automation
 * off, or Lutron differing from the intent), "Back to automatic" first, as the primary, then that switch. While the
 * Lutron state, the intent or automation is unknown nothing says the light matches, so Back to automatic shows too, with
 * both switches when the Lutron state is the unknown one. A command on its way keeps both switches, so the other one
 * can still override it.
 */
export function lightButtons(item: Pick<LightItem, "intent" | "state">, automation: number | null | undefined, pending: boolean): { readonly automatic: boolean; readonly turns: readonly LightState[] } {
  const matching = automation === 1 && known(item.intent) && known(item.state) && item.intent === item.state;
  const turns: readonly LightState[] = pending || !known(item.state) ? ["on", "off"] : [item.state === "on" ? "off" : "on"];
  return { automatic: !matching, turns };
}

/** "Automatic" or "Automation off" from the switch's device value; a value not reported reads "—" with the API's reason, never off. */
export function automationWords(row: Pick<Setting, "readback"> | undefined): string {
  const value = row?.readback?.value;
  if (value === null || value === undefined) return row?.readback?.reason ? `Automation — ${row.readback.reason}` : "Automation —";
  return value === 0 ? "Automation off" : "Automatic";
}

/**
 * The row's automatic line, for every role, from the switch's API-09 row (60 s, 5 s while REQUESTED): REQUESTED, NOT
 * CONFIRMED or HELD while the API says so, CONFIRMED once this control's own write reads it, then the device value in
 * words. With no row to read it says "—" rather than its last words.
 */
function AutomationLine({ control }: { control: LightControl }) {
  const { setting, writeId } = control;
  const state = setting?.state ?? "";
  const pending = state === "requested" || state === "not_confirmed" || state === "held";
  const own = writeId !== null && setting?.desired?.id === writeId && state === "confirmed";
  return <p className="light-auto" data-auto={state || "none"}>
    {(pending || own) && <><ReadbackBadge state={state} />{" "}</>}
    {!pending && automationWords(setting)}
    {pending && <StateReasonLine state={state} reason={setting?.desired?.state_reason} requestedAt={setting?.desired?.created_at} />}
  </p>;
}

/**
 * What a light row gains (SCR-07, US-EQP-05): its automatic line with the link to that switch's setting drawer, opened
 * in place (`settingTo`, for every role: a viewer's is read-only), and for operators "Activity ›" beside it; then, for
 * operators only, the buttons its state calls for (`lightButtons`): Back to automatic first while the light is
 * overridden, and the switch to its other state, at 44 px, full width on phones, disabled with changes switched off. One
 * tap and no dialog. The board's state only ever comes from the Lutron readback; here are what was said and what is
 * awaited: a 503's `Problem.result` in words, and after 30 s without a match "Lutron hasn't confirmed: still OFF".
 */
export function LightControls({ control, item, settingTo }: { control: LightControl; item: Pick<LightItem, "label" | "state" | "intent" | "manual_until" | "auto_reason">; settingTo?: string }) {
  const until = manualUntil(item);
  const reason = item.auto_reason ?? null;
  return <div className="light-controls">
    {until !== null && <p className="state-note" data-light-until="">{item.state === "on" ? "On" : item.state === "off" ? "Off" : "Switched"} {manualUntilWords(until)}, then back to automatic</p>}
    {reason !== null && <p className="state-note meta" data-light-reason={reason}>Controller: {words("light_reason", reason)}</p>}
    <div className="light-auto-row">
      <AutomationLine control={control} />
      <span className="light-links">
        {settingTo && <Link className="light-auto-link" to={settingTo} preventScrollReset>Automation setting <span className="screen-reader">of {item.label}</span><span aria-hidden="true">›</span></Link>}
        <OperatorOnly>
          <Link className="light-auto-link" to={`/activity/log?target=${encodeURIComponent(control.equipment)}`}>Activity <span className="screen-reader">of {item.label}</span><span aria-hidden="true">›</span></Link>
        </OperatorOnly>
      </span>
    </div>
    <OperatorOnly><LightButtons control={control} item={item} /></OperatorOnly>
  </div>;
}

/**
 * A light's buttons and what they await, shared by Equipment's Lights and Now's quick actions (#1342, as D-UX-05 shares
 * the bursts), so the two pages cannot disagree: the buttons `lightButtons` picks, each Turn on or Turn off timed
 * (`MANUAL_MINUTES`, #1335), then "Waiting for Lutron", 30 s later "Lutron hasn't confirmed: still OFF", a 503's words,
 * or a refused write. `wide` makes them full width on phones (Equipment); `compact` keeps Now's row short. Operators only:
 * the caller wraps it in `OperatorOnly`.
 */
export function LightButtons({ control, item, compact = false }: { control: LightControl; item: Pick<LightItem, "label" | "state" | "intent">; compact?: boolean }) {
  const { command, write, pressed } = control;
  const busy = command.sending || write.sending;
  // Reading back: a second tap on the same button has nothing to add, though after a refusal it may retry.
  const answering = (state: LightState) => command.phase === "waiting" && pressed === state && !command.error;
  const shown = lightButtons(item, control.setting?.readback?.value, command.sending || (command.phase === "waiting" && !command.error));
  const turn = (state: LightState) => <Button key={state} write wide={!compact} busy={command.sending && pressed === state} disabled={busy || answering(state)} onClick={() => control.turn(state)}>
    {state === "on" ? "Turn on" : "Turn off"} <span className="burst-length">{MANUAL_MINUTES} min</span> <span className="screen-reader">{item.label}</span>
  </Button>;
  return <>
    <div className="actions light-actions">
      {shown.automatic && <Button write wide={!compact} busy={write.sending || (command.sending && pressed === "automatic")} disabled={busy || control.awaitingConfirm} onClick={() => void control.backToAutomatic()}>Back to automatic <span className="screen-reader">{item.label}</span></Button>}
      {shown.turns.map(turn)}
    </div>
    {shown.automatic && !compact && <p className="state-note" data-light-timed="">A Turn on or Turn off lasts {MANUAL_MINUTES} min, then the light goes back to automatic</p>}
    {command.phase === "waiting" && !command.error && <p className="state-note" data-light-note="waiting">Waiting for Lutron</p>}
    {command.phase === "late" && <p className="state-note" data-light-note="late">{unconfirmedWords(item.state)}</p>}
    {control.unread && <p className="state-note" data-light-note="unread">Couldn't read the light, so Lutron was not matched to the controller</p>}
    {command.error && <p className="field-error" role="alert">{command.error.message}</p>}
    {write.error && <p className="field-error" role="alert">{write.error.message}</p>}
  </>;
}

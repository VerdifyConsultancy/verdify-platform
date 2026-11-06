// SPDX-License-Identifier: MIT
/**
 * Now's quick actions (redesign §5.1, D-UX-05): the caretaker's on-site actions in one row, for operators only. Stop all
 * water comes first (full width on a phone, above the fold at 390 × 844), then the three bursts the Home Assistant
 * buttons ran: Fans 15 min, Humidify 5 min and Vent bypass 10 min, each one API-IRR-12 with no dialog. What runs is
 * only ever the controller's readback (API-02 `overrides`), which "Running and next" shows with Clear. Before the
 * irrigation firmware API-IRR-12 answers 503, so the row holds Stop all water and says when the bursts arrive. The
 * bursts stay on Equipment too. "Bursts" is a `Term` (#1339) that says what each does.
 * Then the lights (#1342): Main light and Grow light, each with its Lutron state and automation in words and the buttons
 * Equipment's Lights shows (`LightButtons`, one component, so the pages cannot disagree): a timed Turn on or Turn off
 * through API-20 (15 min, #1335) and Back to automatic. They read API-13 every 15 s and API-09 every 60 s, mount only for
 * operators, and do not wait for the irrigation firmware. Viewers get no row at all.
 */
import { Link } from "react-router";
import type { GlossaryId } from "../../lib/glossary";
import type { Equipment, IrrigationStatus, Setting } from "../../lib/operator-api";
import { useEquipmentRead, useSettingsRead, type PanelRead } from "../../lib/queries";
import { OperatorOnly } from "../../lib/session";
import { BURSTS, BURSTS_ARRIVE_TEXT, BurstButton, burstsIgnoredText } from "../burst-panel";
import { Button } from "../button";
import { LIGHTS, LightButtons, automationWords, lightsDiffer, manualUntil, manualUntilWords, useLightControl, type LightControl } from "../equipment/light-controls";
import { StopAllWater, type StopAllControl } from "../stop-all-water";
import { Term } from "../term";

/** The shared burst button (Equipment's too): its name over its length on a phone, so three fit side by side. */
function QuickBurst({ burst }: { burst: (typeof BURSTS)[number] }) {
  return <div className="now-burst"><BurstButton burst={burst} className="now-burst-button" /></div>;
}

/** The bursts, or why they are not here yet: while the engine flag is unknown the row keeps its height. */
function Bursts({ irrigation, mode }: { irrigation: PanelRead<IrrigationStatus>; mode: string | null }) {
  if (irrigation.failed && !irrigation.data) {
    return <p className="meta now-actions-note">Can't tell whether the controller has the irrigation firmware yet. <Button onClick={irrigation.retry}>Retry</Button></p>;
  }
  const engine = irrigation.data?.engine ?? null;
  if (engine === null) return <div className="skeleton now-bursts-skeleton" aria-hidden="true" />;
  if (!engine) return <p className="meta now-actions-note" data-arrives="bursts">{BURSTS_ARRIVE_TEXT}</p>;
  const ignored = burstsIgnoredText(mode);
  return <>
    <div className="now-bursts">{BURSTS.map((burst) => <QuickBurst key={burst.kind} burst={burst} />)}</div>
    <p className="meta now-actions-term"><Term id="bursts">What the bursts do</Term></p>
    {ignored && <p className="fault now-actions-note" data-bursts="ignored">{ignored}</p>}
  </>;
}

/** Each light on Now, its glossary entry and its name. */
const LIGHT_NAMES: Readonly<Record<string, { readonly name: string; readonly term: GlossaryId }>> = {
  main: { name: "Main light", term: "main_light" },
  grow: { name: "Grow light", term: "grow_light" },
};
const settingOf = (rows: readonly Setting[] | undefined, param: string) => rows?.find((row) => row.param === param);

/** "off · Automatic", "on · Automation off", "on · by hand until 10:57 · the controller wants it off"; "—" until API-13 answers. */
export function lightStateWords(item: { readonly state: string; readonly intent?: string | null; readonly manual_until?: string | null } | undefined, setting: Pick<Setting, "readback"> | undefined): string {
  if (!item) return "—";
  const state = item.state === "on" || item.state === "off" ? item.state : "state unknown";
  const differs = lightsDiffer(item.intent, item.state) ? ` · the controller wants it ${item.intent}` : "";
  // A Turn on or Turn off still running (#1377's timer) reads its end, not the automation switch, which is still on.
  const until = manualUntil({ state: item.state, intent: item.intent ?? null, manual_until: item.manual_until ?? null });
  return `${state} · ${until !== null ? manualUntilWords(until) : automationWords(setting)}${differs}`;
}

/** One light: its name (a Term), its state in words, and the shared buttons. */
function QuickLight({ control, item, light }: { control: LightControl; item: Pick<Equipment["items"][number], "state" | "intent" | "manual_until"> | undefined; light: string }) {
  const { name, term } = LIGHT_NAMES[light];
  return <div className="now-light" data-light={light}>
    <p className="now-light-head"><Term id={term}>{name}</Term> <span className="meta" data-light-state="">{lightStateWords(item, control.setting)}</span></p>
    {item && <LightButtons compact control={control} item={{ label: name, state: item.state, intent: item.intent ?? null }} />}
  </div>;
}

/**
 * The lights group (#1342), mounted for operators only. The controls are held here, above each row, so a failed API-13
 * read loses no command in progress; while it fails the group says so with Retry and offers no button.
 */
function QuickLights() {
  const equipment = useEquipmentRead();
  const settings = useSettingsRead();
  const rows = settings.error ? undefined : settings.data?.items;
  const [main, grow] = LIGHTS;
  const controls = [useLightControl(main, settingOf(rows, main.autoParam)), useLightControl(grow, settingOf(rows, grow.autoParam))];
  const items = equipment.error ? null : equipment.data?.items ?? null;
  return <div className="now-lights" role="group" aria-label="Lights">
    {equipment.error && !equipment.data
      ? <p className="meta now-actions-note">Can't read the lights. <Button onClick={() => void equipment.refetch()}>Retry</Button></p>
      : items === null
        ? <div className="skeleton now-bursts-skeleton" aria-hidden="true" />
        : LIGHTS.map((light, index) => <QuickLight key={light.light} light={light.light} control={controls[index]} item={items.find((item) => item.equipment === light.equipment)} />)}
    <p className="meta now-lights-more">
      <Term id="light_timer">Turn on and off last 15 min</Term> · <Link to="/equipment#lights">Lights<span className="screen-reader"> in Equipment</span><span aria-hidden="true">{"\u00a0›"}</span></Link>
    </p>
  </div>;
}

export type QuickActionsProps = {
  /** The route's own `useStop`, so its reads speed up while the Stop confirms. */
  readonly stop: StopAllControl & { readonly confirming: boolean };
  readonly irrigation: PanelRead<IrrigationStatus>;
  /** API-02 `mode.code`, for "Bursts are ignored during emergency cooling". */
  readonly mode: string | null;
};

export function QuickActions({ stop, irrigation, mode }: QuickActionsProps) {
  return <OperatorOnly>
    <section className="now-actions" aria-label="Quick actions">
      <div className="now-stop">
        <StopAllWater wide control={stop} />
        {stop.confirming && <span className="meta">Waiting for the valves to read off</span>}
      </div>
      <Bursts irrigation={irrigation} mode={mode} />
      <QuickLights />
    </section>
  </OperatorOnly>;
}

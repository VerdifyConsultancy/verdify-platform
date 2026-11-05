// SPDX-License-Identifier: MIT
import { Fragment } from "react";
import { causeWords } from "../../charts/causes";
import type { IrrigationStatus } from "../../lib/operator-api";
import { useShellNow, useShellStatus } from "../../lib/shell";
import type { PolledQueryResult } from "../../lib/use-current";
import { nextRunOf, pauseUntilWords, programBanner, runningLines } from "../../lib/water";
import { Badge } from "../badge";
import { Button } from "../button";
import { cls } from "../common";
import { Ago } from "../freshness";
import { lineTitle } from "../schedule/model";

/**
 * What the controller runs, the first part of Water's status line (#1135, redesign §5.2) with `[data-program]`:
 * "Program rev 42 on controller · confirmed 2 min ago"; "rev 43 sending" with REQUESTED while a change travels; "Not
 * confirmed: <reject in words>"; the built-in program; no engine yet; the controller not reporting; or "Water status
 * unavailable." with Retry while API-IRR-01 fails, never idle.
 */
export function ProgramState({ irrigation, short = null }: { irrigation: PolledQueryResult<IrrigationStatus>; short?: "age" | "word" | null }) {
  const status = useShellStatus();
  const now = useShellNow();
  const banner = programBanner({
    failing: irrigation.isError, status: irrigation.data ?? null, ingestorRunning: status.ingestorRunning,
    controllerReporting: status.controllerReporting, climateAt: now.data?.freshness.climate.observed_at,
  });
  if (banner.kind === "loading") return null;
  const revision = irrigation.data?.engine ? irrigation.data.program.applied_revision ?? irrigation.data.program.revision : null;
  // On a phone (redesign §5.2, §6.7): "Rev 42 on controller · 2 min ago" on Today, "Rev 42 confirmed" before the next run
  // on Schedules; a controller not reporting keeps its first sentence, the header's banner saying the rest.
  const confirmed = short === "age" ? <>Rev {revision ?? "—"} on controller · {banner.confirmedAt ? <Ago at={banner.confirmedAt} /> : "—"}</>
    : short === "word" ? <>Rev {revision ?? "—"} confirmed</>
    : <>Program rev {revision ?? "—"} on controller · confirmed {banner.confirmedAt ? <Ago at={banner.confirmedAt} /> : "—"}</>;
  const text = banner.kind === "confirmed" ? confirmed
    : short && banner.kind === "not_reporting" ? banner.text.replace(/\. .*$/, "")
    : banner.text.replace(/^On controller: rev (\d+) · /, "Program rev $1 on controller · ");
  // The `[data-program]` hook is the full line's; the phone's copy carries none, so the hook stays one element.
  return <span className={cls("wat-program", banner.tone !== "info" && `wat-program-${banner.tone}`)} data-program={short ? undefined : banner.kind}>
    {text}
    {banner.badge === "requested" && <> <Badge kind="requested" /></>}
    {banner.kind === "unavailable" && <> <Button className="wat-retry" onClick={() => void irrigation.refetch()}>Retry</Button></>}
  </span>;
}

/** "L04 Center mister running (VPD)", each linked to its row on Today; "nothing running" when none is. */
function Running({ status, linked }: { status: IrrigationStatus; linked: boolean }) {
  const running = runningLines(status.lines);
  if (!running.length) return <>nothing running</>;
  return <>{running.map((line, index) => {
    const words = <>{lineTitle(line)} running{line.state.cause ? ` (${causeWords(line.state.cause)})` : ""}</>;
    return <Fragment key={line.line_id}>{index > 0 && (index === running.length - 1 ? " and " : ", ")}{linked ? <a href={`#line-${line.line_id}`}>{words}</a> : words}</Fragment>;
  })}</>;
}

/**
 * Water's status line by view (redesign §5.2, §6.1): the program state, then on Today whatever is running (with links to
 * its rows) and on Schedules the next run ("next: L01 Wall drip 16:00"). History's period summary comes from its view.
 */
export function WaterStatus({ irrigation, view, today, summary, short = false }: { irrigation: PolledQueryResult<IrrigationStatus>; view: "today" | "schedules" | "history"; today: string; summary?: string | null; short?: boolean }) {
  if (view === "history") return summary ? <span className="wat-summary">{summary}</span> : null;
  const status = irrigation.data;
  const program = <ProgramState irrigation={irrigation} short={short ? (view === "today" ? "age" : "word") : null} />;
  if (!status || irrigation.isError || !status.engine) return program;
  // A phone's Today line is the program alone: each running line's row says so, toned, right under it.
  if (view === "today") return short ? program : <>{program} · <Running status={status} linked /></>;
  const next = nextRunOf(status.lines);
  return <>{program} · {next ? <>next{short ? "" : ":"} {lineTitle(next.line)} {pauseUntilWords(next.next.start_at, today)}</> : "no run planned this week"}</>;
}

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
export function ProgramState({ irrigation }: { irrigation: PolledQueryResult<IrrigationStatus> }) {
  const status = useShellStatus();
  const now = useShellNow();
  const banner = programBanner({
    failing: irrigation.isError, status: irrigation.data ?? null, ingestorRunning: status.ingestorRunning,
    controllerReporting: status.controllerReporting, climateAt: now.data?.freshness.climate.observed_at,
  });
  if (banner.kind === "loading") return null;
  const revision = irrigation.data?.engine ? irrigation.data.program.applied_revision ?? irrigation.data.program.revision : null;
  const text = banner.kind === "confirmed"
    ? <>Program rev {revision ?? "—"} on controller · confirmed {banner.confirmedAt ? <Ago at={banner.confirmedAt} /> : "—"}</>
    : banner.text.replace(/^On controller: rev (\d+) · /, "Program rev $1 on controller · ");
  return <span className={cls("wat-program", banner.tone !== "info" && `wat-program-${banner.tone}`)} data-program={banner.kind}>
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
export function WaterStatus({ irrigation, view, today, summary }: { irrigation: PolledQueryResult<IrrigationStatus>; view: "today" | "schedules" | "history"; today: string; summary?: string | null }) {
  if (view === "history") return summary ? <span className="wat-summary">{summary}</span> : null;
  const status = irrigation.data;
  const program = <ProgramState irrigation={irrigation} />;
  if (!status || irrigation.isError || !status.engine) return program;
  if (view === "today") return <>{program} · <Running status={status} linked /></>;
  const next = nextRunOf(status.lines);
  return <>{program} · {next ? <>next: {lineTitle(next.line)} {pauseUntilWords(next.next.start_at, today)}</> : "no run planned this week"}</>;
}

// SPDX-License-Identifier: MIT
import { Component, createContext, useContext, useId, type ReactNode } from "react";
import { Link } from "react-router";
import { duration } from "../lib/format";
import { useApiDown } from "../lib/session";
import { Badge } from "./badge";
import { Button } from "./button";
import { LocalTime, cls } from "./common";
import { AgeLabel, FreshnessDot } from "./freshness";
import { reasonParts } from "./state-reason-line";

/**
 * What a panel shows (screens §1.5). `loading` is the first fetch only: while a refetch runs, pass the state of the
 * data already shown, so the old render stays. A viewer has no state of its own: write controls render only inside
 * `<OperatorOnly>`, so a viewer's panel simply has none.
 */
export type PanelState = "ready" | "loading" | "empty" | "stale" | "ingestor_down" | "controller_not_reporting" | "error" | "busy";
const DEVICE_STALE: readonly PanelState[] = ["stale", "ingestor_down", "controller_not_reporting"];

/** The enclosing panel's freshness, so a tile shows its own only when it differs, and whether a verdict may show. */
export type PanelInfo = { readonly stale: boolean; readonly observedAt?: string | null; readonly verdict: boolean };
export const PanelInfoContext = createContext<PanelInfo>({ stale: false, observedAt: undefined, verdict: true });
export const usePanelInfo = (): PanelInfo => useContext(PanelInfoContext);

/** "Couldn't load runs." with Retry, in the failing panel only. */
export function PanelError({ thing, text, onRetry }: { thing: string; text?: ReactNode; onRetry?: () => void }) {
  return <div className="panel-error"><p className="fault">{text ?? `Couldn't load ${thing}.`}</p>{onRetry && <Button onClick={onRetry}>Retry</Button>}</div>;
}

/** Catches a render failure inside one panel, so the other panels keep working. Retry renders it again. */
export class PanelErrorBoundary extends Component<{ thing: string; text?: ReactNode; onRetry?: () => void; children?: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  retry = () => { this.setState({ failed: false }); this.props.onRetry?.(); };
  render() {
    return this.state.failed ? <PanelError thing={this.props.thing} text={this.props.text} onRetry={this.retry} /> : this.props.children;
  }
}

/** "No runs today." plus what would appear here and a link that widens the range. */
export function EmptyState({ message, detail, widen }: { message: ReactNode; detail?: ReactNode; widen?: { to: string; label: ReactNode } }) {
  return <div className="panel-empty"><p>{message}</p>{detail && <p className="muted">{detail}</p>}{widen && <p><Link to={widen.to}>{widen.label}</Link></p>}</div>;
}

function ReasonSince({ code, since }: { code: "writer_down" | "controller_not_reporting"; since: string | null | undefined }) {
  return <span>{reasonParts({ code, since }).map((part, index) => (typeof part === "string" ? part : <LocalTime key={index} at={part.time} />))}</span>;
}

export type PanelProps = {
  title: ReactNode;
  eyebrow?: ReactNode;
  headingLevel?: 2 | 3;
  id?: string;
  /** The panel's own controls, beside the heading. */
  actions?: ReactNode;
  /** What the panel loads, for "Couldn't load <thing>." and the empty default ("No <thing>."). */
  thing: string;
  state: PanelState;
  /** The skeleton's reserved height while loading. */
  reserve?: "sm" | "md" | "lg";
  /** stale: when the newest data was observed. */
  observedAt?: string | null;
  /** ingestor_down: the newest heartbeat; controller_not_reporting: the newest climate row. */
  since?: string | null;
  /** busy: the `Retry-After` seconds. */
  retryInS?: number | null;
  /** error, and the boundary's Retry. */
  onRetry?: () => void;
  /** Replaces "Couldn't load <thing>." (an alert list reads "Alerts unavailable."). */
  errorText?: ReactNode;
  /** empty: what would appear here (`EmptyState`). */
  empty?: ReactNode;
  children?: ReactNode;
};

/**
 * A card that loads one thing and says honestly how it is: a skeleton at its reserved height while loading; the empty
 * words; STALE with "Last data N min ago (HH:MM)" and no verdict; the ingestor or controller line with device values
 * STALE; its own error and Retry; "Busy, retrying in N s" over the old render. A render failure inside stays here.
 */
export function Panel({ title, eyebrow, headingLevel = 2, id, actions, thing, state, reserve = "md", observedAt, since, retryInS, onRetry, errorText, empty, children }: PanelProps) {
  const headingId = useId();
  const apiDown = useApiDown();
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const stale = DEVICE_STALE.includes(state);
  const info: PanelInfo = { stale, observedAt, verdict: !stale && !apiDown };
  const line = state === "stale" ? <><FreshnessDot stale /><Badge kind="stale" /><AgeLabel observedAt={observedAt} stale /></>
    : state === "ingestor_down" || state === "controller_not_reporting" ? <><FreshnessDot stale notReporting /><Badge kind="stale" /><ReasonSince code={state === "ingestor_down" ? "writer_down" : "controller_not_reporting"} since={since} /></>
    : state === "busy" ? <span>Busy, retrying {retryInS !== null && retryInS !== undefined && Number.isFinite(retryInS) ? <>in {duration(Math.max(0, retryInS))}</> : "shortly"}</span>
    : null;
  const body = state === "loading" ? <><div className={`skeleton skeleton-${reserve}`} aria-hidden="true" /><p className="screen-reader">Loading {thing}</p></>
    : state === "error" ? <PanelError thing={thing} text={errorText} onRetry={onRetry} />
    : state === "empty" ? empty ?? <EmptyState message={`No ${thing}.`} />
    : <PanelErrorBoundary thing={thing} text={errorText} onRetry={onRetry}>{stale ? <div className="panel-muted">{children}</div> : children}</PanelErrorBoundary>;
  return <PanelInfoContext.Provider value={info}>
    <section id={id} className={cls("card", "panel")} aria-labelledby={headingId} aria-busy={state === "loading" || undefined} data-state={state}>
      <div className="card-head"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<Heading id={headingId}>{title}</Heading></div>{actions}</div>
      {line && <p className="panel-state">{line}</p>}
      {body}
    </section>
  </PanelInfoContext.Provider>;
}

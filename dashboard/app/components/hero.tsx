// SPDX-License-Identifier: MIT
import { useId, type ReactNode } from "react";
import { formatValue } from "../lib/format";
import { useApiDown } from "../lib/session";
import { Badge } from "./badge";
import { cls } from "./common";
import { AgeLabel, FreshnessDot } from "./freshness";
import { PanelInfoContext, type PanelInfo } from "./panel";
import { StatList, StatTile, type StatTileProps } from "./stat-tile";

/** A hero tile. `phone` marks the (at most four) tiles that stay in the band on phones. */
export type HeroTile = StatTileProps & { name: string; phone?: boolean };
const PHONE_TILES = 4;

export type HeroProps = {
  eyebrow: ReactNode;
  /** The title slot: Now's one-sentence answer, worst first (#1129). */
  title: ReactNode;
  headingLevel?: 1 | 2;
  /** The hero value (indoor temperature) and its unit; null is "—" with `reason`. */
  value: number | null | undefined;
  unit: string;
  reason?: string | null;
  /** The value's verdict against the band; shown only on a value that is there, fresh and with the API up. */
  verdict?: ReactNode;
  /**
   * Readings that sit beside the hero value where their tiles move away (Now: VPD and RH at 860 px and below, `.hero-beside`).
   * The caller leaves verdicts out of it while the hero is stale.
   */
  beside?: ReactNode;
  sub?: ReactNode;
  /** The hero source's newest observation, whether the API marks it stale, and whether its source is not reporting. */
  observedAt: string | null | undefined;
  stale?: boolean;
  notReporting?: boolean;
  /** Eight tiles, 4 × 2; on phones the four marked `phone` (else the first four) stay, 2-up. */
  tiles: readonly HeroTile[];
  /** More detail for the "All readings" disclosure, such as a table of every reading with its time. */
  readings?: ReactNode;
  readingsLabel?: ReactNode;
};

function phoneTiles(tiles: readonly HeroTile[]): Set<number> {
  const marked = tiles.flatMap((tile, index) => (tile.phone ? [index] : [])).slice(0, PHONE_TILES);
  return new Set(marked.length ? marked : tiles.slice(0, PHONE_TILES).map((_, index) => index));
}

/**
 * Weather's ink hero: an eyebrow, the answer as the title, the hero value with its sub-lines and freshness, and the
 * tiles. On phones the tiles not kept in the band move into the "All readings" disclosure (copied there without their
 * ids). It fetches nothing: Now (#1129) passes the API's values in.
 */
export function Hero({ eyebrow, title, headingLevel = 1, value, unit, reason, verdict, beside, sub, observedAt, stale = false, notReporting = false, tiles, readings, readingsLabel = "All readings" }: HeroProps) {
  const headingId = useId();
  const apiDown = useApiDown();
  const Heading = headingLevel === 1 ? "h1" : "h2";
  const panelStale = stale || notReporting;
  const info: PanelInfo = { stale: panelStale, observedAt, verdict: !panelStale && !apiDown };
  const missing = value === null || value === undefined || !Number.isFinite(value);
  const kept = phoneTiles(tiles);
  const moved = tiles.filter((_, index) => !kept.has(index));
  return <PanelInfoContext.Provider value={info}>
    <section className="hero" aria-labelledby={headingId}>
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <Heading id={headingId} className="hero-answer">{title}</Heading>
        <p className={cls("hero-temp", panelStale && "value-muted")} data-hero-value="">{formatValue(missing ? null : value, unit)}</p>
        {missing && <p className="hero-meta">{reason || "No reading"}</p>}
        {verdict && !missing && info.verdict && <p className="hero-meta">{verdict}</p>}
        {beside && <p className={cls("hero-meta", "hero-beside", panelStale && "value-muted")}>{beside}</p>}
        {sub && <p className="hero-meta">{sub}</p>}
        <p className="hero-meta"><FreshnessDot stale={stale} notReporting={notReporting} /><AgeLabel observedAt={observedAt} stale={panelStale} />{panelStale && <> <Badge kind="stale" /></>}</p>
      </div>
      <StatList variant="ink">{tiles.map((tile, index) => <StatTile key={tile.name} {...tile} className={cls(tile.className, !kept.has(index) && "stat-more")} />)}</StatList>
      {(readings || moved.length > 0) && <details className={readings ? undefined : "hero-more-phone"}>
        <summary>{readingsLabel}</summary>
        {moved.length > 0 && <StatList variant="ink" className="stats-phone">{moved.map((tile) => <StatTile key={tile.name} {...tile} id={undefined} />)}</StatList>}
        {readings}
      </details>}
    </section>
  </PanelInfoContext.Provider>;
}

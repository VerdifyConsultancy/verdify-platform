// SPDX-License-Identifier: MIT
import { Link } from "react-router";
import type { IrrigationStatus } from "../../lib/operator-api";
import { BACKUP_TERM, BUDGET_HELP, BUDGET_TERM, SAF4_HELP, SAF4_PRIMARY, SAF4_TERM, flowWords, occupancyWords } from "../../lib/water";
import { Card } from "../card";
import { cls } from "../common";
import { Meter, meterWords } from "../data/meter";
import { useNow } from "../freshness";
import { InfoTip } from "../info-tip";

/** Where the daily mist & fog limit leads: the site caps save through API-10 in the Settings drawer, not on Water (#1182). */
export const SAF4_LINK = "/settings?edit=mister_daily_volume_max_gal";

/**
 * Site today (redesign §5.2, #1349), Water → Today's aside: the daily mist & fog limit (SAF-4, the primary daily water
 * limit, #1339) and the daily mist target (soft) as meters in whole gallons, each with an info tip that says what it is, both in `--eq-water` as §4.6 fills
 * gallons; then water with no Verdify valve open, occupancy and backup misting, each in words with its `[data-limit]`.
 * No bare "SAF-4" or "SOFT BUDGET". A count the API does not have reads "—", never 0; a flow or backup misting that is the
 * news carries its colour on its words. On a phone (§5.2's 360 px sketch) it holds the limit, the flow and occupancy, so
 * the first line card stays above the fold: the soft target and backup misting (`.wat-site-quiet`) show there only once
 * they are the news.
 */
export function SiteToday({ status, today }: { status: Pick<IrrigationStatus, "limits" | "leak" | "occupancy" | "fallback_active">; today: string }) {
  const now = useNow();
  const flow = flowWords(status.leak);
  const { saf4_gal: saf4, soft_budget_gal: budget } = status.limits;
  const budgetReached = meterWords(budget.used, budget.max, "gal").over !== null;
  return <Card title="Site today" className="wat-site">
    <ul className="wat-site-list">
      <li className="wat-site-meter" data-limit="saf4">
        <span className="wat-site-term"><Link to={SAF4_LINK}>{SAF4_TERM}<span className="screen-reader">, in Settings</span></Link><InfoTip term={SAF4_TERM}><p>{SAF4_HELP}</p></InfoTip><span className="wat-site-primary meta">{SAF4_PRIMARY}</span></span>
        <Meter label={`${SAF4_TERM}, gallons today`} unit="gal" value={saf4.used} cap={saf4.max} domain="water" showWords />
      </li>
      <li className={cls("wat-site-meter", !budgetReached && "wat-site-quiet")} data-limit="budget">
        <span className="wat-site-term">{BUDGET_TERM}<InfoTip term={BUDGET_TERM}><p>{BUDGET_HELP}</p></InfoTip></span>
        <Meter label={`${BUDGET_TERM}, gallons today`} unit="gal" value={budget.used} cap={budget.max} domain="water" showWords />
      </li>
      <li data-limit="flow" className={cls(flow.tone && `limit-${flow.tone}`)}>{flow.text}</li>
      <li data-limit="occupancy">{occupancyWords(status.occupancy, now, today)}</li>
      <li data-limit="fallback" className={cls(status.fallback_active ? "limit-warn" : "wat-site-quiet")}>{status.fallback_active ? `${BACKUP_TERM}: on, VPD sensing is degraded` : `${BACKUP_TERM}: off`}</li>
    </ul>
  </Card>;
}

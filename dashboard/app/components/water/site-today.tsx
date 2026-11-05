// SPDX-License-Identifier: MIT
import { Link } from "react-router";
import type { IrrigationStatus } from "../../lib/operator-api";
import { SAF4_WORDS, flowWords, occupancyWords } from "../../lib/water";
import { Card } from "../card";
import { cls } from "../common";
import { Meter, meterWords } from "../data/meter";
import { useNow } from "../freshness";

/** Where the SAF-4 meter leads: the site caps save through API-10 in the Settings drawer, not on Water (#1182). */
export const SAF4_LINK = "/settings?edit=mister_daily_volume_max_gal";

/**
 * Site today (redesign §5.2), Water → Today's aside: SAF-4 (with what it caps in words, as Now says it) and the soft
 * budget as meters in whole gallons, both in `--eq-water` as §4.6 fills gallons, the flow with
 * every Verdify valve closed, occupancy and fallback misting, each in words and each with its `[data-limit]`. A count the
 * API does not have reads "—", never 0; a flow or fallback that is the news carries its colour on its words. On a phone
 * (§5.2's 360 px sketch) it holds SAF-4, the flow and occupancy, so Wall and L01 stay above the fold: the soft budget and
 * fallback misting (`.wat-site-quiet`) show there only once they are the news, the budget reached or fallback on.
 */
export function SiteToday({ status, today }: { status: Pick<IrrigationStatus, "limits" | "leak" | "occupancy" | "fallback_active">; today: string }) {
  const now = useNow();
  const flow = flowWords(status.leak);
  const { saf4_gal: saf4, soft_budget_gal: budget } = status.limits;
  const budgetReached = meterWords(budget.used, budget.max, "gal").over !== null;
  return <Card title="Site today" className="wat-site">
    <ul className="wat-site-list">
      <li className="wat-site-meter" data-limit="saf4">
        <Link className="wat-site-term" to={SAF4_LINK}>SAF-4<span className="screen-reader">, the {SAF4_WORDS}, in Settings</span></Link>
        <Meter label="SAF-4 gallons today" unit="gal" value={saf4.used} cap={saf4.max} domain="water" showWords />
        <span className="meta wat-site-note" aria-hidden="true">{SAF4_WORDS}</span>
      </li>
      <li className={cls("wat-site-meter", !budgetReached && "wat-site-quiet")} data-limit="budget">
        <span className="wat-site-term">Soft budget</span>
        <Meter label="Soft budget gallons today" unit="gal" value={budget.used} cap={budget.max} domain="water" showWords />
      </li>
      <li data-limit="flow" className={cls(flow.tone && `limit-${flow.tone}`)}>{flow.text}</li>
      <li data-limit="occupancy">{occupancyWords(status.occupancy, now, today)}</li>
      <li data-limit="fallback" className={cls(status.fallback_active ? "limit-warn" : "wat-site-quiet")}>{status.fallback_active ? "Fallback misting on: VPD sensing is degraded" : "Fallback misting: off"}</li>
    </ul>
  </Card>;
}

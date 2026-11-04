// SPDX-License-Identifier: MIT
import { Link } from "react-router";
import type { IrrigationStatus } from "../../lib/operator-api";
import { flowWords, occupancyWords } from "../../lib/water";
import { Card } from "../card";
import { cls } from "../common";
import { Meter } from "../data/meter";
import { useNow } from "../freshness";

/** Where the SAF-4 meter leads: the site caps save through API-10 in the Settings drawer, not on Water (#1182). */
export const SAF4_LINK = "/settings?edit=mister_daily_volume_max_gal";

/**
 * Site today (redesign §5.2), Water → Today's aside: SAF-4 and the soft budget as meters in whole gallons, the flow with
 * every Verdify valve closed, occupancy and fallback misting, each in words and each with its `[data-limit]`. A count the
 * API does not have reads "—", never 0; a flow or fallback that is the news carries its colour on its words.
 */
export function SiteToday({ status, today }: { status: Pick<IrrigationStatus, "limits" | "leak" | "occupancy" | "fallback_active">; today: string }) {
  const now = useNow();
  const flow = flowWords(status.leak);
  const { saf4_gal: saf4, soft_budget_gal: budget } = status.limits;
  return <Card title="Site today" className="wat-site">
    <ul className="wat-site-list">
      <li className="wat-site-meter" data-limit="saf4">
        <Link className="wat-site-term" to={SAF4_LINK}>SAF-4<span className="screen-reader">, the daily mist volume cap, in Settings</span></Link>
        <Meter label="SAF-4 gallons today" unit="gal" value={saf4.used} cap={saf4.max} domain="mister" showWords />
      </li>
      <li className="wat-site-meter" data-limit="budget">
        <span className="wat-site-term">Soft budget</span>
        <Meter label="Soft budget gallons today" unit="gal" value={budget.used} cap={budget.max} domain="water" showWords />
      </li>
      <li data-limit="flow" className={cls(flow.tone && `limit-${flow.tone}`)}>{flow.text}</li>
      <li data-limit="occupancy">{occupancyWords(status.occupancy, now, today)}</li>
      <li data-limit="fallback" className={cls(status.fallback_active && "limit-warn")}>{status.fallback_active ? "Fallback misting on: VPD sensing is degraded" : "Fallback misting: off"}</li>
    </ul>
  </Card>;
}

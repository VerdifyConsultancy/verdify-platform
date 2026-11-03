// SPDX-License-Identifier: MIT
import { PageHead } from "../components/common";
export const meta = () => [{ title: "Water schedules · Verdify dashboard" }];
/** SCR-04 Water schedules: its page head until #1135 fills it. No greenhouse data renders here or in SSR: data loads in the browser. */
export default function WaterSchedules() {
  return <PageHead eyebrow="Verdify dashboard" title="Water schedules" lede="Not built yet." />;
}

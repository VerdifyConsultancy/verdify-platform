// SPDX-License-Identifier: MIT
import { PageHead } from "../components/common";
export const meta = () => [{ title: "Water history · Verdify dashboard" }];
/** SCR-04 Water history: its page head until #1135 fills it. No greenhouse data renders here or in SSR: data loads in the browser. */
export default function WaterHistory() {
  return <PageHead eyebrow="Verdify dashboard" title="Water history" lede="Not built yet." />;
}

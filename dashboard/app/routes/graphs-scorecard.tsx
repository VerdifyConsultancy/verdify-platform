// SPDX-License-Identifier: MIT
import { PageHead } from "../components/common";
export const meta = () => [{ title: "Daily scorecard · Verdify dashboard" }];
/** SCR-02 Daily scorecard: its page head until #1130 fills it. No greenhouse data renders here or in SSR: data loads in the browser. */
export default function Scorecard() {
  return <PageHead eyebrow="Verdify dashboard" title="Daily scorecard" lede="Not built yet." />;
}

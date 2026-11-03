// SPDX-License-Identifier: MIT
import { PageHead } from "../components/common";
export const meta = () => [{ title: "Plan · Verdify dashboard" }];
/** SCR-03 Plan: its page head until #1131 fills it. No greenhouse data renders here or in SSR: data loads in the browser. */
export default function Plan() {
  return <PageHead eyebrow="Verdify dashboard" title="Plan" lede="Not built yet." />;
}

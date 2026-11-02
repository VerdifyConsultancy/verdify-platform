// SPDX-License-Identifier: MIT
import { PageHead } from "../components/common";
export const meta = () => [{ title: "Activity · Verdify dashboard" }];
/** SCR-06 Activity: its page head until #1133 fills it. No greenhouse data renders here or in SSR: data loads in the browser. */
export default function Activity() {
  return <PageHead eyebrow="Verdify dashboard" title="Activity" lede="Not built yet." />;
}

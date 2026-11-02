// SPDX-License-Identifier: MIT
import { PageHead } from "../components/common";
export const meta = () => [{ title: "Water · Verdify dashboard" }];
/** SCR-04 Water: its page head until #1135 fills it. No greenhouse data renders here or in SSR: data loads in the browser. */
export default function Water() {
  return <PageHead eyebrow="Verdify dashboard" title="Water" lede="Not built yet." />;
}

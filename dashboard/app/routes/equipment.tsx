// SPDX-License-Identifier: MIT
import { PageHead } from "../components/common";
export const meta = () => [{ title: "Equipment · Verdify dashboard" }];
/** SCR-07 Equipment: its page head until #1134 fills it. No greenhouse data renders here or in SSR: data loads in the browser. */
export default function Equipment() {
  return <PageHead eyebrow="Verdify dashboard" title="Equipment" lede="Not built yet." />;
}

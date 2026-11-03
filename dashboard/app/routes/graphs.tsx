// SPDX-License-Identifier: MIT
import { PageHead } from "../components/common";
export const meta = () => [{ title: "Graphs · Verdify dashboard" }];
/** SCR-02 Graphs: its page head until #1130 fills it. No greenhouse data renders here or in SSR: data loads in the browser. */
export default function Graphs() {
  return <PageHead eyebrow="Verdify dashboard" title="Graphs" lede="Not built yet." />;
}

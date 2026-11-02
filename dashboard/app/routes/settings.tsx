// SPDX-License-Identifier: MIT
import { PageHead } from "../components/common";
export const meta = () => [{ title: "Settings · Verdify dashboard" }];
/** SCR-05 Settings: its page head until #1132 fills it. No greenhouse data renders here or in SSR: data loads in the browser. */
export default function Settings() {
  return <PageHead eyebrow="Verdify dashboard" title="Settings" lede="Not built yet." />;
}

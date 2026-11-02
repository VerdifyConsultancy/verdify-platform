// SPDX-License-Identifier: MIT
export const meta = () => [{ title: "Now · Verdify dashboard" }];
/** The page head only. No greenhouse data renders here or in SSR: data loads in the browser (architecture §3). */
export default function Now() {
  return <div className="page-head"><div><span className="eyebrow">Verdify dashboard</span><h1>Now</h1><p className="lede">Not built yet.</p></div></div>;
}

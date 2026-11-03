// SPDX-License-Identifier: MIT
import { Link } from "react-router";
import { BuildLine } from "../components/app-shell";
import { PageHead } from "../components/common";
import { MORE_SECTIONS, SignOutForm } from "../components/site-header";
import { useMe, useShell } from "../lib/shell";

export const meta = () => [{ title: "More · Verdify dashboard" }];

/**
 * The phone's More tab (screens §1.1): the sections the bottom bar has no room for, then who is signed in with Sign
 * out, then the build line. The header's name and Sign out move here at 860 px and below.
 */
export default function More() {
  const me = useMe();
  const shell = useShell();
  const name = me ? me.display_name || me.username : null;
  return <>
    <PageHead eyebrow="Verdify dashboard" title="More" />
    <ul className="more-list">
      {MORE_SECTIONS.map((item) => <li key={item.to}><Link to={item.to}>{item.label}<span aria-hidden="true"> ›</span></Link></li>)}
    </ul>
    <div className="more-session">
      {name && <p>Signed in as {name}</p>}
      <SignOutForm />
    </div>
    <p className="small"><BuildLine build={shell.build} api={me?.source_revision ?? null} /></p>
  </>;
}

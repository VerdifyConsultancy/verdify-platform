// SPDX-License-Identifier: MIT
import { useMe } from "../lib/shell";
import { Menu } from "./menu";

/** The role from API-01: OPERATOR or VIEWER (the stylesheet uppercases it). */
export function RolePill({ role }: { role: string }) {
  const operator = role === "operator";
  return <span className="tag role-pill" data-role={operator ? "operator" : "viewer"}>{operator ? "Operator" : "Viewer"}</span>;
}

/** Sign out is a plain POST form: the BFF checks its exact Origin, deletes the session and ends at Authentik (#1141). */
export function SignOutForm({ className }: { className?: string }) {
  return <form className={className ? `signout-form ${className}` : "signout-form"} method="post" action="/auth/logout"><button type="submit">Sign out</button></form>;
}

/**
 * The account button at the header's right edge (redesign §2.2, D-UX-01): the role pill at every width and, from
 * 1100 px, the display name. It opens a disclosure with who is signed in, what the role may do and Sign out. Before
 * API-01 answers it reads "Account" and still holds Sign out, so a session can always end; nothing about the person
 * renders on the server.
 */
export function AccountMenu() {
  const me = useMe();
  const name = me ? me.display_name || me.username : null;
  const operator = me?.role === "operator";
  const label = me
    ? <><RolePill role={me.role} />{name && <span className="account-name">{name}</span>}<span className="screen-reader"> account</span></>
    : <span className="account-name account-unknown">Account</span>;
  return <Menu kind="button" className="account-menu" label={label}>
    {me && <p className="account-who">Signed in as {name}{name !== me.username && <> <span className="mono">({me.username})</span></>}</p>}
    {me && <p className="account-role meta">{operator ? "Operator: can change anything" : "Viewer: read-only"}</p>}
    <SignOutForm className="account-signout" />
  </Menu>;
}

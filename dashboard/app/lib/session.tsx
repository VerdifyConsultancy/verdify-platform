// SPDX-License-Identifier: MIT
import { createContext, useContext, useMemo, type ReactNode } from "react";

/**
 * Who is signed in and whether the dashboard can write, from API-01 (`role`, `writes_enabled`), and whether the
 * dashboard API is down (the shell's API-02 poll failed twice in a row). #1124's shell provides it; until then every
 * page is a viewer with writes off. The UI is not the gate: the API refuses a viewer's write with 403.
 */
export type Role = "operator" | "viewer";
export type Session = { readonly role: Role; readonly writesEnabled: boolean; readonly apiDown: boolean };
export const DEFAULT_SESSION: Session = { role: "viewer", writesEnabled: false, apiDown: false };
export const SessionContext = createContext<Session>(DEFAULT_SESSION);

export function SessionProvider({ role = DEFAULT_SESSION.role, writesEnabled = DEFAULT_SESSION.writesEnabled, apiDown = DEFAULT_SESSION.apiDown, children }: Partial<Session> & { children?: ReactNode }) {
  const value = useMemo(() => ({ role, writesEnabled, apiDown }), [role, writesEnabled, apiDown]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export const useSession = (): Session => useContext(SessionContext);
export const useRole = (): Role => useSession().role;
/** `writes_enabled` from API-01: false disables every write control but Stop (design system §4.5). */
export const useWritesEnabled = (): boolean => useSession().writesEnabled;
/** True while the dashboard API is down: nothing green shows (screens §1.2). */
export const useApiDown = (): boolean => useSession().apiDown;

/** Renders its children for an operator and nothing for a viewer: a viewer never sees a write control. */
export function OperatorOnly({ children }: { children?: ReactNode }) {
  return useRole() === "operator" ? <>{children}</> : null;
}

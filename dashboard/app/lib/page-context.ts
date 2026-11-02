// SPDX-License-Identifier: MIT
import { createContext } from "react-router";

/**
 * SCR-09's sign-in refusals: the reason #1141's `/auth/*` handlers give, the status the page answers with and its one
 * card. `failed` covers a state mismatch, an expired login cookie or an Authentik error; `unavailable`, Authentik out
 * of reach; `no_access`, an account in neither dashboard group.
 */
export const SIGN_IN_REFUSALS = {
  failed: { status: 400, title: "Sign-in didn't complete", text: "Try again.", action: "sign_in" },
  unavailable: { status: 503, title: "Sign-in is unavailable", text: "Try again in a minute.", action: "sign_in" },
  no_access: {
    status: 403, title: "No dashboard access", text: "Your account doesn't have dashboard access. Ask Jason to add you to a dashboard group.",
    action: "sign_out",
  },
} as const;
export type SignInRefusalReason = keyof typeof SIGN_IN_REFUSALS;
/** `res.locals.authError` as #1141 leaves it: the refusal and the request id its log line carries. */
export type AuthError = { readonly reason: SignInRefusalReason; readonly requestId: string | null };

/**
 * What the BFF hands React Router for one page request (server.mjs `getLoadContext`): the build revision for the
 * footer, the request id the error page shows, and a sign-in refusal, if any, as #1141's handlers left it. The server
 * build exports this key from entry.server.tsx, so server.mjs sets the same object the root loader reads.
 */
export type PageContext = { readonly sourceRevision: string; readonly requestId: string | null; readonly authError: unknown };
export const pageContext = createContext<PageContext>({ sourceRevision: "unknown", requestId: null, authError: null });

const REQUEST_ID = /^[A-Za-z0-9-]{1,64}$/;
const own = (value: object, key: string) => Object.hasOwn(value, key);

/** A refusal this build words, or null: anything else from `res.locals` is ignored, never rendered raw. */
export function signInRefusal(value: unknown): AuthError | null {
  if (!value || typeof value !== "object") return null;
  const { reason, requestId } = value as { reason?: unknown; requestId?: unknown };
  if (typeof reason !== "string" || !own(SIGN_IN_REFUSALS, reason)) return null;
  return { reason: reason as SignInRefusalReason, requestId: typeof requestId === "string" && REQUEST_ID.test(requestId) ? requestId : null };
}

/** A request id fit to show: a UUID-like token, else null. */
export function displayableRequestId(value: unknown): string | null {
  return typeof value === "string" && REQUEST_ID.test(value) ? value : null;
}

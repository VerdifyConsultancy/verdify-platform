// SPDX-License-Identifier: MIT
import { data, type LoaderFunctionArgs, type MetaFunction } from "react-router";
import { SIGN_IN_REFUSALS, pageContext, signInRefusal } from "../lib/page-context";

/**
 * `/auth/*` (SCR-09). #1141's BFF answers sign-in, the callback and sign-out itself. When it refuses a sign-in it hands
 * the request on with `res.locals.authError`, and the root renders that card in place of this route, at the same URL
 * and with the refusal's status. Any other `/auth` path that reaches the app is not found.
 */
export function loader({ context }: LoaderFunctionArgs) {
  if (!signInRefusal(context.get(pageContext).authError)) throw data(null, { status: 404 });
  return null;
}

export const meta: MetaFunction = ({ matches }) => {
  const root = matches.find((match) => match?.id === "root")?.loaderData as { authError?: unknown } | undefined;
  const refusal = signInRefusal(root?.authError);
  return [{ title: `${refusal ? SIGN_IN_REFUSALS[refusal.reason].title : "Sign in"} · Verdify dashboard` }];
};

/** The root shows the card; this route adds nothing. */
export default function Auth() {
  return null;
}

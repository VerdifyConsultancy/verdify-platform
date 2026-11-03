# Dashboard access runbook (dashboard.verdify.ai)

Who can sign in to the dashboard, and with which role, is decided by two Authentik groups and nothing else
(`docs/design/dashboard/architecture.md` §4). Adding or moving a person needs no change on the dashboard's side.
Authentik steps run in its admin interface, `https://auth.vallery.net/if/admin/`. Operations:
[dashboard runbook](dashboard.md).

| Authentik group | Role | Can |
|---|---|---|
| `verdify-dashboard-operators` | operator | Read, and every write (settings, irrigation, alerts) |
| `verdify-dashboard-viewers` | viewer | Read only: write controls are not rendered, and the API refuses writes with 403 |
| Both | operator | As above |
| Neither | none | Denied at Authentik, and the BFF refuses to create a session |

## Add a caretaker

This takes Authentik steps only. Until jvallery/agents#5754 and jvallery/agents#5755 land, Authentik identifies
people only through Google, so a caretaker needs a `vallery.net` or `verdify.ai` Google identity.

1. **A user.** Directory → Users: find the person by that Google address. If they have never signed in to a Vallery
   app, create a user with their username, name and that address as its email.
2. **One dashboard group.** Directory → Groups: add the user to `verdify-dashboard-viewers` or
   `verdify-dashboard-operators`, whichever role Jason gives them.
3. **An invitation link.** While sign-in is Google-only, the link is `https://dashboard.verdify.ai/` itself: the
   normal flows offer no enrolment path that an Authentik invitation could use. Once jvallery/agents#5754 and
   jvallery/agents#5755 land, it is the one-use link from `issue_enrol_link.py --user <username>`
   (jvallery/agents `platform/kubernetes/migration/auth/login-standard/`; the coordinator runs it at home).
4. **First sign-in.** Google, then the member-tier flow has them enrol a second factor (a passkey on their own phone,
   or TOTP), then Now with their role pill in the header. There is no dashboard-side setup.

A first sign-in that ends on the "No dashboard access" card, or on Authentik's permission page, means that identity's
Authentik user is in neither group. Look in Directory → Users for a second user with the same email: Google sign-in
can enrol a new user instead of linking to the one you created. Put the group on the user the sign-in uses, and
deactivate the spare.

## Change a role

Move the user from one group to the other, so they are in exactly one (both means operator). It applies at their next
sign-in, within 12 h. To apply it sooner, delete their sessions, which ends them within 30 s:

```bash
scripts/verdify-db.sh prod -c "DELETE FROM dashboard_session WHERE username = '<username>';"
```

`<username>` is their Authentik username, the `preferred_username` claim. Their next request gets 401, the page asks
them to sign in again, and the new sign-in carries the new role. To see who holds a session, with no token material:

```bash
scripts/verdify-db.sh prod -c "SELECT username, role, created_at, expires_at FROM dashboard_session ORDER BY created_at DESC;"
```

## Remove access

Do all three steps. The first two stop new sign-ins, but an open session keeps working until it expires, up to 12 h,
because the operator API checks the session row and not Authentik.

1. Remove the user from both dashboard groups.
2. Deactivate the user (Directory → Users). This ends their access to every Authentik application, so if they keep
   other apps, Jason decides whether it stays active; the first step alone already denies the dashboard at Authentik.
3. Delete their sessions with the command above. They end within 30 s.

Writes they made stay in Activity under their name.

# WP5 — Connections: Gmail, Calendar, and the connection layer

Status: built and verified on sample data (branch `wp5-connections`).
Live accounts are connected at the pilot stage (WP6), not here.

## What WP5 delivers

Phase 1 connects one inbox and one calendar through **direct Google APIs**
via the foundation's built-in `google_api` provider. The paid hosted
connector removed in WP1 is not used anywhere in either route, and the
removal scan still passes with the connection layer in place.

Because no real account may be connected in this stage, WP5 builds the
layer properly and proves it against a seeded stand-in:

| Piece | Where | What it does |
|---|---|---|
| Sample provider | `shared/scripts/providers/sample_google_workspace.py` | Implements the full `WorkspaceClient` surface (mail, calendar, files, contacts) over seeded pilot-shaped data. Registered as provider `sample_google`; holds no credentials; chosen explicitly by config, never a silent fallback. |
| Capability row | `shared/scripts/workspace_capabilities.py` | `sample_google` declares exactly the actions it implements, so the WP4 capability gate treats it like a real provider. |
| Config template | `shared/config/connections.google.yaml.example` | One file per owner. References credentials by env-var name / file path only — never a secret value. Declares the least-access scopes per capability. |
| Health checks | `shared/scripts/connection_health.py` | Structured report: what is configured, what is missing, and the next step in plain language. Credential files are checked by existence only; secret values never appear in the report. A live probe runs only when `connections.allow_live_probe: true` **and** every local check passes. |
| Verification flow | `shared/scripts/wp5_flow.py` | The seven-step WP5 gate (below), run with `python shared/scripts/wp5_flow.py`. |

## The seeded sample data

Shaped exactly like the Phase 1 test script's seed, for the pilot business
(Moe J Diamonds, sample `.example` addresses throughout):

- **Today:** 10:00 diamond viewing (Sarah Linden); 15:00 appointment
  (Marcus Bell, engagement ring fitting).
- **Tomorrow:** 13:30 supplier call (Patel Gems); 16:00 appointment
  (Aisha Rahman, anniversary band).
- **Inbox:** one urgent email needing a decision today (held insured
  shipment); one from a named sender (Daniel Okafor) whose body carries a
  specific figure (USD 4,250) and a Thursday-delivery question; one routine
  newsletter; one read follow-up thread.

The sample provider speaks the Gmail search dialect the briefing queries
use (`is:unread`, `from:`, OR groups, `newer_than:`, quoted phrases), so the
daily loop's own collectors run against it unchanged.

## The seven-step gate (all passing)

1. **Health** — the report is green in sample mode and says plainly that no
   real account is connected.
2. **Brief** — the four brief parts assembled through the daily loop's
   collectors reading through the provider: today's + tomorrow's schedule,
   unread email needing attention, the pending pipeline follow-up, and
   (after step 4) pending approvals.
3. **Named email** — fetched in full; the figure in its body matches the
   seed (nothing invented).
4. **Draft** — a reply is queued in the Review Queue with the full preview
   (what, why, risk, how to reverse) and the outbox is provably empty.
5. **Send** — signed approval, then the WP4 guarded path (claim → account
   check → capability check → single-use grant → record). A second
   execution attempt is refused at the claim stage; exactly one message
   exists.
6. **Move** — the 15:00 appointment moves to tomorrow 16:30 through the
   same guarded path; exactly one copy exists, at the new time.
7. **Audit** — both actions end `executed` with four audit entries each and
   the approver recorded.

Build order inside the package followed the plan: calendar read first,
Gmail read second, guarded writes last, Activepieces last (see
`docs/activepieces-integration.md`).

## Auth modes

The template supports the two honest routes to a Google account:

- **`oauth_user` (recommended for owners).** The owner signs in once with
  Google; the host stores a per-owner token (encrypted at rest, one file
  per owner instance). Works for gmail.com and Workspace accounts alike.
- **`service_account` (Workspace domains only).** The foundation's existing
  route: a service account with domain-wide delegation, authorised by the
  domain's admin. Uses the legacy `google.*` keys the current provider
  already reads.

Least-access scopes (declared in the template, one per capability):
`gmail.readonly` (brief + read one email), `gmail.compose` (drafts),
`gmail.send` (guarded send only), `calendar.readonly` (brief + schedule),
`calendar.events` (guarded move only).

## Disconnect

`connection_health.py --disconnect` performs the local half today: it
deletes the stored token file and writes the disconnect to the audit log.
The Google-side half — revoking the grant — is a live-stage step and is
reported as remaining, never silently assumed (see the checklist below).

## Branding: the consent screen must say "My Work Pal"

The owner's hard rule: connecting a tool presents as **My Work Pal /
JobOS Labs**, never a third party. On the Google side, the name an owner
sees at consent is the **app name on the OAuth consent screen of the
Google Cloud project that owns the OAuth client**. So the client must be
ours. Precisely:

### Only Ibrahim can do these (bound to his organisation's accounts)

1. Create the Google Cloud project under the JobOS Technologies
   organisation (e.g. `my-work-pal-prod`) and be its owner.
2. Verify ownership of the `jobos.us` domain (Google Search Console) so it
   can be an authorised domain on the consent screen.
3. In that project: configure the OAuth consent screen — user type
   External; **app name exactly "My Work Pal"**; support email a JobOS Labs
   address; app logo (JobOS Labs logo, 120×120); app domain
   `https://labs.jobos.us`; authorised domain `jobos.us`; developer
   contact email.
4. Create the OAuth client (Web application) in that project, with the
   host's redirect URI(s) we provide, and hand the client ID and secret
   over **through the secure flow only** (never chat, email, or the repo).
5. Pilot stage: keep the app in **Testing** publishing status and add each
   pilot owner as a test user (≤100). This needs no Google verification —
   but the consent screen shows an "unverified app" notice.
6. Before public launch: submit the app for Google verification. The
   Calendar scopes and `gmail.compose`/`gmail.send` are *sensitive* scopes
   (verification required). `gmail.readonly` is a *restricted* scope:
   verification **plus** a CASA security assessment, renewed annually.
   Budget for that before launch; it is the real cost of the Gmail route.
7. Workspace route only: if an owner is a Google Workspace domain using
   `service_account` mode, **their domain admin** authorises the service
   account's client ID and scopes under Admin Console → Security → API
   controls → Domain-wide delegation. Only that admin can do it.

### Already handled on our side

- The config template with the exact scope list and env-name references.
- Health checks that report precisely which of the steps above are done.
- The disconnect's local half + audit entry.
- (WP6 prep, built) the OAuth connect flow itself —
  `shared/scripts/google_oauth.py`: authorisation URL on the registered
  redirect URI, code exchange, per-owner token storage (file `0o600`,
  directory `0o700`), refresh, and the Google-side revocation call for
  disconnect. The owner steps that remain are in
  `docs/wp6-google-sitting.md`.

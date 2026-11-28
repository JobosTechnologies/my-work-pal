# WP7 — Wiring runbook: connecting the foundation to real data

This runbook takes My Work Pal from sample data to the pilot owner's real
inbox, calendar, and tools. Every step is marked **[owner]** (only the
owner can do it) or **[agent]** (done by the build agent, no owner time
needed). Only Step 1 needs the owner, in one sitting.

Pilot account: **jobostech@gmail.com** (JobOS's own company Google
account; decided Nov 27–28, 2026 — it is already a test user on the
"My Work Pal" OAuth consent screen, which is in Testing mode).
Pilot config template: `shared/config/connections.pilot.yaml.example`
(token path `~/.myworkpal/jobos/google-token.json`, auth mode
`oauth_user`, the five least-access scopes).

Prerequisites (already true as of Nov 28, 2026):

- The "My Work Pal Web" OAuth client exists in Google Cloud project
  `my-work-pal-prod`, with the redirect URI
  `https://labs.jobos.us/oauth/google/callback` registered under its
  Authorized redirect URIs, and JavaScript origins empty.
- A client ID + secret were captured into the Secure Vault on Nov 27.
  **The values are never read back out of the vault into chat, files, or
  logs** — at the sitting they are placed into the host's environment as
  `MWP_GOOGLE_OAUTH_CLIENT_ID` / `MWP_GOOGLE_OAUTH_CLIENT_SECRET` through
  the approved secure flow.
- `shared/scripts/google_oauth.py --help` loads clean, and its `url`
  subcommand was verified on Nov 28 with throwaway dummy placeholder
  values in a one-off shell environment (never written to any file): it
  produced a well-formed consent URL carrying the registered redirect
  URI, all five scopes, `access_type=offline`, `prompt=consent`, and
  `login_hint=jobostech@gmail.com`.

---

## Step 1 [owner, one sitting] — the Google sign-in

1. The agent builds the authorization URL on the host:
   `python shared/scripts/google_oauth.py --config <pilot connections.yaml> url`
   and hands the owner the URL (it contains no secret).
2. The owner opens the URL in the browser where **jobostech@gmail.com**
   is signed in, and signs in as jobostech@gmail.com if asked.
3. Google shows the "Google hasn't verified this app" notice — expected,
   because the consent screen is in Testing mode and the app is ours.
   The owner clicks **Advanced → Go to My Work Pal**.
4. The owner reviews the five scopes (read mail, draft, send, read
   calendar, manage calendar events) and clicks **Allow**.
5. The browser redirects to
   `https://labs.jobos.us/oauth/google/callback?code=...&state=...`.
   That domain does not serve a page yet, so the browser shows an error
   page — that is fine. **The code is in the address bar.** The owner
   copies the full callback URL (or just the `code` value) and pastes it
   back to the agent. The code is short-lived and single-use; it is not
   a password and is never stored.

**Proves it worked:** the callback URL carries a `code` parameter and no
`error` parameter, and the `state` value matches what the `url` command
generated for this sitting.

**Could block it:** signing in with the wrong Google account (the URL's
`login_hint` pre-fills jobostech@gmail.com — the owner must not switch
accounts); the consent screen leaving Testing mode or the test-user list
changing (jobostech@gmail.com must stay a test user); the owner closing
the tab before copying the callback URL (recoverable: run `url` again
and repeat — old codes simply expire).

## Step 2 [agent] — exchange the code, store the token, health check

1. Exchange: `python shared/scripts/google_oauth.py --config <pilot connections.yaml> exchange --code <code from Step 1>`.
   The token is written atomically to
   `~/.myworkpal/jobos/google-token.json` with file permissions `0o600`
   (directory `0o700`). The client secret is used in memory only and
   never appears in output or logs.
2. Flip `connections.allow_live_probe` to `true` in the pilot owner's
   real connections file (never in the `.example` template).
3. Run the real health check:
   `python shared/scripts/connection_health.py --config <pilot connections.yaml>`
   and report all 9 checks. (The `--config-only` dry run against the
   template passes 9/9 shape checks as of Nov 28; the real run must also
   pass the live probe against Google.)

**Proves it worked:** token file exists with `0o600` permissions and a
refresh token inside; health check exits 0 with every check `ok`,
including the live probe; a read-only Gmail profile call returns the
jobostech@gmail.com address.

**Could block it:** code expired or already used (codes live ~10
minutes — redo Step 1; it costs the owner two minutes); client secret
mismatch (the Nov 27 secret rotation left two enabled secrets — if the
exchange fails with `invalid_client`, the vault value belongs to the
other secret; resolve by capturing the currently-enabled secret again,
never by guessing); consent screen changes since Nov 27.

## Step 3 [agent] — first real run, draft-only

1. Run the morning brief against the REAL inbox and calendar for
   jobostech@gmail.com (briefing pipeline, read-only scopes).
2. Generate one review-queue proposal from real data (e.g. a follow-up
   draft for a real thread) and park it in the Review Queue.
3. Walk the approval path with a test approval: confirm the gateway
   signs only a clear yes, the send executes exactly once through
   `gmail.send`, and a second yes does not double-send. Confirm an
   unclear reply sends nothing.

**Proves it worked:** the brief reflects the real calendar and real
inbox counts; the proposal sits in `requested` state until approved;
the audit log shows proposal → approval → single execution; nothing
left the mailbox before approval.

**Could block it:** the `google_api` provider still needs its
user-token execution path confirmed against this first real token
(known wiring-day item from WP9: the provider historically executed via
the service-account route — verify it acts as jobostech@gmail.com, and
if it cannot, that fix lands before anything else in this step);
an empty or near-empty pilot inbox makes the brief trivially thin
(acceptable for the pilot — the gate test matters more than content).

## Step 4 [agent] — stand up Activepieces on the Railway host

Activepieces is the connection layer for everything beyond Gmail and
Calendar (778-piece catalog, MIT, self-hosted so owners see only the
My Work Pal / JobOS Labs name). **Do not attempt this in the build
sandbox — it has no Docker runtime.** Per
`docs/activepieces-integration.md`, on the Railway host (project
"my-work-pal", the one already running the voice worker):

1. Add a new service to the Railway project from the Docker image
   `activepieces/activepieces:latest`.
2. Set the pilot environment: `AP_DB_TYPE=PGLITE`,
   `AP_REDIS_TYPE=MEMORY`, `AP_FRONTEND_URL=https://<the service's Railway domain>`
   (production/multi-owner later moves to the docker-compose topology
   with PostgreSQL + Redis behind our own subdomain).
3. Attach a public domain/port so the service serves on port 80.
4. Open the instance, create the JobOS Labs admin account, and create
   one project for the pilot owner (project-per-owner isolation).
5. Configure the platform OAuth apps on our instance so connection
   consent screens name My Work Pal, never a third party.

**Proves it worked:** the instance loads at its URL; the pilot project
exists; one benchmark piece (e.g. QuickBooks or Slack) can be connected
in the pilot project and its actions appear as tools on the project's
MCP URL.

**Could block it:** Railway memory on the current plan (the container
wants ~2 GB free RAM — check the plan before starting; upgrading the
plan is an owner payment decision, flagged, never done silently); the
duplicate "my-work-pal" Railway project trap — work only in the live
project that runs the voice worker; pieces needing their own OAuth app
registration before owners can connect them.

## Step 5 [agent] — wire the skills, money-first

Wire in the order set by
`workspace/my-work-pal-research/suggestion-engine-brief.md` (Tier 1
money leaks first; its detector build order governs). Each skill gets
the same verification gate: **detector runs read-only against real
data → card/proposal shows real figures recomputed on that run →
nothing executes without the approval gateway → audit log entry.**

1. **Bookkeeper — overdue invoice chase** (Detector 1): AR listing
   from the real books; the card names invoice, client, amount, days
   overdue. Gate: a chase draft is generated and held; no send.
2. **Pipeline — stale deal rescue** (Detector 3): deals idle past the
   14-day threshold, combined dollar value on the card. Gate: one
   follow-up draft per stale deal, each approved separately.
3. **Email triage** (Detectors 9, then 2/4/5): classifier counts and
   the thread-state join (who wrote last) over the real inbox — inbox
   tidy, invoices hiding in email, quote-sent-no-reply, unanswered
   client email. Gate: labels/drafts only; sends stay gated.
4. **Calendar & meetings** (Detector 10): pre-meeting briefs pulling
   the open item for each meeting from pipeline/bookkeeper state.
   Gate: brief renders with real open items; event moves stay gated.
5. **Documents + signing**: templates filled and filed; e-signature
   through self-hosted **Documenso** (the settled route, Nov 27 —
   AGPL, REST API + webhooks, sealed PDF + audit trail filed back to
   the owner's workspace; signing on behalf of a person only within
   permission granted in advance, scoped and revocable). Gate: a test
   document goes out for signature and the sealed copy + audit trail
   land back in the workspace.

**Proves it worked (per skill):** the gate above, plus the suggestion
card's "Why this job" figures matching a hand-check of the source data
on first run.

**Could block it:** bookkeeping/pipeline data still in sample form
(the pilot business's real records must be loaded before Detectors 1
and 3 can fire — loading them is owner-data work, scheduled with the
owner, never fabricated); Documenso not yet stood up (same Docker host
as Step 4 — stand it up alongside Activepieces); thread-state join
quality on a messy inbox (Detector tier 2 lands only when the join
proves accurate on the pilot inbox).

---

## After wiring

- The pilot acceptance gate (WP6 Part G) runs on real calls: six
  scenarios, three consecutive passes each, any send without read-back
  + approval fails the whole gate.
- Outbound calling stays parked until the owner reopens it (Telnyx
  route is documented and ready; it is not part of this runbook).

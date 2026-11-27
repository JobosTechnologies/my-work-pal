# Activepieces — stand-up result, catalog verification, and the MCP pattern

Activepieces (MIT, open source) is the connection layer for everything
beyond Gmail and Calendar: owners connect their own tools inside their own
workspace, and our agent layer reaches those tools through Activepieces'
MCP server. This note records what was verified for WP5 and what the
pilot (WP6) builds on.

## Stand-up in this sandbox: blocked — precisely why

Activepieces was **not** stood up locally. The official install is a
single Docker container (`activepieces/activepieces:latest`, embedded
PGLite database + in-memory queue; production uses PostgreSQL + Redis).
Per the project's own install docs: "Requirements: Docker, and nothing
else." This sandbox has:

- **No container runtime**: `docker`, `podman`, `nerdctl`, `buildah`,
  `runc` — none installed, none on PATH.
- **No way to install one in reasonable time**: `apt-get update` was run
  to install podman; after 35+ minutes the package lists were still not
  fetched (3 list files present, apt cache unchanged) — the sandbox's
  egress throttles the apt mirrors to a crawl. The attempt was abandoned
  and recorded rather than retried silently.
- **Tight memory**: ~8 GB total on the VM with under 1 GB free while the
  foundation test suite runs — not a sane host for the container plus a
  build toolchain even if a runtime had landed.

**What it takes on real hosting (JobOS Labs server):** any Docker host
with ~2 GB free RAM. Pilot: the one documented command —
`docker run -d -p 8080:80 -e AP_DB_TYPE=PGLITE -e AP_REDIS_TYPE=MEMORY -e AP_FRONTEND_URL=https://<host> activepieces/activepieces:latest`.
Production / multi-owner: the project's docker-compose topology with
PostgreSQL and Redis, behind our reverse proxy on a JobOS Labs subdomain.
Nothing about the design depends on the sandbox run; the catalog and MCP
surface below were verified from the project's own repository and docs
(main branch, fetched 2026-11-27).

## Catalog verification — the benchmark pieces

Read from the Activepieces repository itself (`packages/pieces/community/`,
main branch, 2026-11-27): piece `package.json`, `src/index.ts`, and the
auth definition for each. The repo's community tree holds **749 piece
directories**; the public catalog counts 778 apps.

| App | Piece (version) | Exists | Auth | Notable |
|---|---|---|---|---|
| QuickBooks Online | `@activepieces/piece-quickbooks` (0.3.5) | ✅ | OAuth2 via Intuit + a required **Company ID** property | 12 actions (create/find invoice, customer, payment, bill, vendor, expense, AR/AP aging reports) + custom API call; 8 triggers (new invoice/expense/customer/deposit/transfer/bill, payment received, new-or-updated invoice). A separate `quickbooks-sandbox` piece exists for testing. |
| Slack | `@activepieces/piece-slack` (0.23.6) | ✅ | OAuth2, **or** direct bot/user token | The token route means a connection can be made with a bot token without an OAuth app round-trip. |
| Notion | `@activepieces/piece-notion` (0.8.4) | ✅ | OAuth2 (api.notion.com) **or** internal integration secret | The internal-integration route is simpler but only sees pages/databases shared with that integration. |
| Google Drive | `@activepieces/piece-google-drive` (0.14.2) | ✅ | OAuth2 via Google; optional service-account (domain-wide delegation) custom auth | Same two routes as our direct Google layer. |
| Google Sheets | `@activepieces/piece-google-sheets` (0.21.1) | ✅ | OAuth2 via Google (+ service-account custom auth) | — |
| Google Docs | `@activepieces/piece-google-docs` (0.7.2) | ✅ | OAuth2 via Google (+ service-account custom auth) | — |
| Dropbox | `@activepieces/piece-dropbox` (0.10.3) | ✅ | OAuth2 via Dropbox | — |
| Zoho CRM | `@activepieces/piece-zoho-crm` (0.4.4) | ✅ | OAuth2 with a required **Location** (data-centre) property; URLs templated `accounts.{location}` | The owner must pick the region their Zoho account lives in (.com/.eu/.in/…); a wrong region fails the connection. |
| Microsoft Outlook | `@activepieces/piece-microsoft-outlook` (0.7.9) | ✅ | OAuth2 via the Microsoft identity platform; `{cloud}/{tenant}` templated, scope set by an access-mode property | Covers Outlook mail/calendar on Microsoft 365 and personal accounts. (For SharePoint/OneDrive depth, sibling pieces `microsoft-*` exist separately.) |
| Google Contacts | `@activepieces/piece-google-contacts` (0.6.2) | ✅ | OAuth2 via Google | People API based. |
| Gmail *(reference)* | `@activepieces/piece-gmail` (0.19.1) | ✅ | OAuth2 via Google (+ service-account custom auth) | Exists, but Phase 1 deliberately keeps Gmail on direct APIs (plan build order); this piece is the going-forward path if that ever changes. |
| Google Calendar *(reference)* | `@activepieces/piece-google-calendar` (0.14.1) | ✅ | OAuth2 via Google (+ service-account custom auth) | Same note as Gmail. |

Every app on the owner's benchmark list exists. No gaps to build in WP5.
(HoneyBook, checked during planning, is **not** in the catalog; if a pilot
owner needs it, it is a piece we would write ourselves — the framework is
open and pieces are TypeScript packages.)

## How pieces reach our agent layer (the MCP pattern)

1. **One instance, hosted by JobOS Labs.** Self-hosted Activepieces on our
   server, on our subdomain, operated by us. Owners never sign up with a
   third-party automation service.
2. **One project per owner** inside that instance (the config template
   already declares `connections.activepieces.project_per_owner: true`).
   A project's connections, flows, and MCP token are isolated from every
   other owner's — the same isolation rule as the foundation core.
3. **Owners connect apps inside their project.** The connection uses
   *platform OAuth apps* configured on our instance (see branding below),
   so consent names My Work Pal. The resulting credentials live in the
   owner's project, encrypted by the instance, never in our code or repo.
4. **The agent calls tools over MCP.** Activepieces exposes each project
   as an MCP server: one URL per project, every connected piece's actions
   available as tools (this is the project's documented model — "a single
   URL that exposes every piece you connect"). Our agent layer connects
   with the per-owner MCP token (referenced by env name in the template:
   `MWP_ACTIVEPIECES_MCP_TOKEN`) and calls e.g. `quickbooks.create_invoice`
   as a tool.
5. **The approval law still governs.** MCP access does not bypass
   Observe → Suggest → Approve → Execute → Audit. Reads (search, list,
   get) may run freely while preparing a brief or a proposal. Any
   consequential write is executed only as an approved pending action —
   the executor calls the MCP tool inside the guarded path, so the
   foundation audit log remains the single record of what was done, by
   whose approval. Trigger-driven Activepieces *flows* (its own
   automations) are not used for owner-facing actions in Phase 1; the
   agent-driven MCP pattern is the documented one (the same pattern the
   plan specifies for Gmail/Calendar, generalised).

WP6/pilot work builds exactly steps 1–4 on the real host, then wires the
first owner (Moe J Diamonds) through QuickBooks + Slack as the proof pair.

## Branding on the Activepieces side

What an owner sees when connecting a tool is decided by **whose OAuth app
the connection uses**, not by Activepieces itself:

### Only Ibrahim can do these (accounts under his organisation)

1. The Google Cloud project + OAuth client (see
   `docs/wp5-connections.md`) — the Google pieces on our instance use this
   same client, so Google consent says "My Work Pal" there too.
2. Designate the JobOS Labs accounts that own the other developer apps
   (Slack app, Intuit/QuickBooks app, Microsoft app registration, Zoho API
   client). The apps are created under those accounts; their names/icons
   are what owners see at consent.

### Already handled on our side (done at deploy time)

- Deploy the instance on a JobOS Labs subdomain with
  `AP_FRONTEND_URL` set to it; create the instance admin via the secure
  flow.
- Register **our** OAuth client ID/secret per piece as platform OAuth
  apps in the instance admin. On a self-hosted instance there are no
  shared vendor cloud apps — connections use the platform apps we
  configure, which is precisely the branding lever: Slack consent shows
  our "My Work Pal" Slack app, QuickBooks consent shows our Intuit app,
  and so on.
- Set the per-owner project structure and issue each project's MCP token
  into that owner's config by env reference.

### Known limit, stated plainly

Full white-labelling of the Activepieces **admin UI itself** (removing
Activepieces' own chrome) is an Enterprise-edition feature; the MIT
community edition keeps its UI. That UI is operator-facing (JobOS Labs
staff). Owners meet connections through our product UI and the
third-party consent screens, which the steps above fully brand as
My Work Pal / JobOS Labs — so the owner's hard rule holds where owners
actually look.

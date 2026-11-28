# My Work Pal — Owner App (v4, built to the design system)

The owner app for My Work Pal, rebuilt on the My Work Pal design
system in `ui/design-system/` (the system Ibrahim uploaded on
Nov 28, 2026). v3's shell and behaviours carry over — in-app call
button, approval queue up front, responsive mobile app layout —
but every color, component and line of copy now follows the
design system: its 26 tokens, its five themes, its components,
and its four interface rules.

## Open it

No build step, no server. Just open the file in a browser:

```
ui/app/index.html
```

## The design system basis

- **Tokens.** All five theme blocks from
  `ui/design-system/tokens.css` are embedded verbatim: 26 color
  tokens per theme (surfaces, ink, amber, teal, green, danger,
  focus, shadows) plus the space, radius and type tokens. No
  component hard-codes a hex value; everything reads a token.
- **Components.** The markup and CSS follow
  `ui/design-system/components/` — Button, Badge, Composer,
  QuickAction, NavItem, ApprovalCard, AgendaList, CallCard,
  JobCard, StatusBar, ThemePicker — using the system's own
  `mwp-` classes.
- **The four rules.** (1) The approval card is always on screen:
  amber border, the full draft, Approve & send / Hold, and
  "WorkPal reads every draft back and waits for your yes. Nothing
  sends without it." (2) Teal is only the phone line — the Talk
  to WorkPal card and the Call chip; nothing else. (3) One amber
  primary action per view. (4) The day comes before the
  dashboard: the agenda names the open item on every meeting
  ("NDA countersignature is the open item").
- **Content.** Short second-person copy, verb buttons, WorkPal
  spelled one word and spoken of in the third person, and the
  Sample data badge next to the logo because this screen runs on
  sample data.

## Themes

The Appearance ThemePicker offers the design system's themes:
**Dark** (default), **Light**, **Midnight**, **Sand**, and **High
contrast**, plus **Match my device**, which follows the operating
system (Dark or Light) and updates live if the device switches.
Each theme defines the same 26 tokens, so amber always means
waiting on you and teal always means the phone line. The choice
applies the moment it is picked — the picker swatches themselves
render in each theme's own colors — and it is saved on the device
(localStorage, key `mwp-theme`) so it is still there next visit.
v3's Light/Dark mode choice is read as a fallback the first time.

The picker sits in the sidebar on desktop and in the Menu view on
phones.

## Suggested jobs: Why this job

New in v4, from Ibrahim's MyCo screenshots (Nov 28, 2026): each
suggested job card carries a **Why this job** section grounded in
the owner's own data, and a **Based on** chip naming the source —
the way MyCo explains its suggestions. The three sample cards:

- **Daily inbox tidy** — over 7,400 unread messages, newsletters
  like TLDR sending 50+ of them. Based on: Newsletter emails.
- **Brand pitch scam check** — four brand and collab pitches
  since April, most unread, several sender domains not matching
  the brands claimed. Based on: Brand pitch emails.
- **TikTok milestone digest** — TikTok's milestone emails are
  the only performance numbers reaching the inbox for Bai Bureh
  News Network, yet they sit unread. Based on: TikTok milestone
  emails.

On this demo screen they are sample data like everything else,
and the section carries the Sample data badge. Per the JobCard
spec, Add job swaps the button for an "Added" badge for three
seconds, then the card leaves the suggestions.

## Layout

Desktop (wide):

- **Left sidebar** (268px, `surface-000`) — My Work Pal by JobOS
  Labs wordmark with the Sample data badge, nav (New, Jobs,
  Library, Client messages, Connected apps, Vault, Contacts), a
  Chats list, the Appearance ThemePicker, the live line status,
  and the account block at the bottom.
- **Center** (at most 680px) — date eyebrow, the greeting with
  one italic amber word, the Ask anything composer, quick-action
  chips (Call WorkPal in teal, Plan today, Draft a follow-up,
  Check my inbox), the status bar (phone line, quiet hours,
  morning call), then Today (agenda) and Talk to WorkPal side by
  side.
- **Right rail** (330px) — "Needs your approval" with the
  ApprovalCard, then "Suggested jobs" with the three JobCards.

Below 1100px the right rail moves under the main column. Below
760px the sidebar collapses: a top app bar, the same content as
views, and a bottom tab bar (Home · Approvals with a badge ·
Call · Menu). The sidebar becomes the Menu view, ThemePicker
included; the right rail becomes the Approvals view.

## Talk to WorkPal: two ways in

- **In-app call (primary).** One press on "Talk to WorkPal" and
  the owner is talking to WorkPal inside the app. In-app calls
  ride the internet, so they cost less than a phone call. The
  button runs a demo state cycle on this screen: Connecting… →
  In call with a running timer → End call → back to idle.
- **By number.** +1 240 231 6158, the real pilot line, sits right
  underneath for calling from any phone. And when the owner is
  away from the app entirely, WorkPal can call their phone
  instead.

## Sample vs real

Sample (from `examples/sample-workspace.json`, the foundation
demos, and the Nov 27–28, 2026 sessions):

- Business name "Sample Co", owner name "Demo Owner"
- 10:00 AM Acme Corp procurement sync, 1:30 PM Beta proposal
  scoping, tomorrow 9:00 AM Gamma Inc kickoff
- The review-queue item (follow-up email to John Tan at Acme
  Corp) and its draft wording
- The three suggested jobs and their Why this job text

Real:

- The phone number. +1 240 231 6158 is the live WorkPal line;
  the Call chip, the tab-bar Call button, and the Call now
  button all dial it.
- The quiet hours (9:00 PM to 7:00 AM), the 8:00 AM morning
  call, and the read-back / approval rule are the system's real
  defaults and guardrails.

## Demo behaviour

This is a static design build. The Approve & send / Hold buttons
record the choice on screen and say plainly that nothing was
sent; sending a message in the composer echoes it with a canned
WorkPal reply; the in-app call button cycles its on-screen
states only — no audio is captured or transmitted, and no real
call is placed. The theme choice is the one thing the page does
save, on the device only (localStorage). Nothing else is sent,
saved, or transmitted by this page. Wiring it to the live
foundation (real accounts, real queue, real approvals, a real
in-app call) is the next step.

v1 stays at `ui/morning-home/` for reference. The design system
itself lives at `ui/design-system/` and is the source this file
was built from; it is not modified by this build.

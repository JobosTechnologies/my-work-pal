# My Work Pal — Owner App (v5: working themes, Settings, calmer)

The owner app for My Work Pal, built on the My Work Pal design
system in `ui/design-system/` (the system Ibrahim uploaded on
Nov 28, 2026). v5 keeps v4's shell and behaviours — in-app call
button, approval queue up front, responsive mobile app layout,
every color and component from the design system — and fixes
the theme picker, moves Appearance into a proper Settings view,
and gives the whole app a calmer, Lindy-weighted pass.

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

## What changed in v5

- **The theme picker actually works now.** In v4 the picker had
  exactly one path: the page's shared script bundle, which also
  carried every demo behaviour. In the sandboxed in-app preview
  Ibrahim opened the file in, the scripts never ran (the same
  sandbox that refused this file over localStorage), so the
  radios were inert HTML and no click could restyle anything.
  v5 gives the theme two independent paths (see Themes below),
  so a switch lands even when scripts or storage are blocked.
- **Appearance moved into Settings.** The picker no longer sits
  in the sidebar or the mobile Menu. A clearly labelled
  **Settings** entry in the sidebar account area (and in the
  mobile Menu, where the account block also opens it) leads to
  a Settings view with three calm sections — Appearance,
  Assistant (name, voice, morning call, quiet hours), Business
  (names, phone line) — one primary action (Done), a quiet Back,
  and generous spacing.
- **Calm pass, weighted to Lindy-style restraint.** No emoji
  anywhere (text and inline SVG only). One amber primary per
  view: the Talk to WorkPal button on the teal call card is now
  a calm light pill in the phone line's own colors, so "Approve
  & send" is the single amber action on the home view. Quiet
  borders, no competing buttons, nothing that looks like an
  AI-generated demo.
- **Two Lindy patterns adopted.** The status area closes with a
  single line naming the one thing that needs the owner ("Just
  one thing needs you — one draft is waiting for your yes."),
  and the approval area carries the three-line trust
  explanation: "WorkPal drafts it · You approve it · Then it
  goes out." Job cards keep the design system's shape: one
  plain verb sentence, the schedule in words, no plumbing.

## Themes

The Appearance ThemePicker lives in **Settings** (see above).
It offers the design system's themes: **Dark** (default),
**Light**, **Midnight**, **Sand**, and **High contrast**, plus
**Match my device**, which follows the operating system (Dark
or Light) and updates live if the device switches. Each theme
defines the same 26 tokens, so amber always means waiting on
you and teal always means the phone line. The picker swatches
themselves render in each theme's own colors.

A switch takes effect the moment a theme is picked, through two
independent layers:

- **Script layer.** The theme wiring is its own small script,
  isolated from the demo behaviours, so nothing else on the
  page can break it. State is held in memory and applied first;
  saving to localStorage (key `mwp-theme`) happens afterwards,
  as a bonus for next visit, and a blocked or throwing storage
  can never block a switch. v3's Light/Dark choice is still read
  as a first-run fallback.
- **CSS layer (no scripts needed).** A generated block at the
  end of the styles restyles the whole app from the checked
  radio alone, using `html:has(...)` selectors carrying the
  same token values (Match my device resolves through the
  operating system's own `prefers-color-scheme`). In a viewer
  where scripts never run, picking a theme still works.

Both layers were verified by scripted checks (jsdom click-through
of every theme, plus a cascade simulation of the CSS layer):
every selection changes the effective `--surface-100` / `--ink`
tokens to that theme's values.

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
  Chats list, the live line status, and the account area at the
  bottom with the Settings entry.
- **Center** (at most 680px) — date eyebrow, the greeting with
  one italic amber word, the Ask anything composer, quick-action
  chips (Call WorkPal in teal, Plan today, Draft a follow-up,
  Check my inbox), the status bar (phone line, quiet hours,
  morning call) closing with the one thing that needs the owner,
  then Today (agenda) and Talk to WorkPal side by side.
- **Right rail** (330px) — "Needs your approval" with the
  ApprovalCard and the three trust lines under it, then
  "Suggested jobs" with the three JobCards.
- **Settings view** — replaces the center column when opened
  (the approval rail stays on screen on desktop): Back, the
  Settings title, then Appearance (the ThemePicker), Assistant,
  and Business sections, and one Done button.

Below 1100px the right rail moves under the main column. Below
760px the sidebar collapses: a top app bar, the same content as
views, and a bottom tab bar (Home · Approvals with a badge ·
Call · Menu). The sidebar becomes the Menu view, which carries
a Settings item; Settings opens as its own view from there.

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

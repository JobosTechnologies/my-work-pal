# My Work Pal — Owner App (v2, professional shell)

The owner app for My Work Pal, redesigned to a professional standard
after v1 (ui/morning-home) was rejected as not professional enough.
The reference bar was MyCo's real app shell — this build works in
the same family (app shell, sidebar, chat center, side panel) but
is its own composition, not a copy: Sabi deep teal and amber on
dark teal-tinted neutrals, Fraunces display type for the greeting,
and a voice-first layout MyCo does not have (call panel, approval
queue up front).

## Open it

No build step, no server. Just open the file in a browser:

```
ui/app/index.html
```

## Layout

Desktop (wide):

- **Left sidebar** — My Work Pal by JobOS Labs brand mark, nav
  (New, Jobs, Library, Client messages, Connected apps, Vault,
  Contacts), a Chats list (Morning brief, Get started), the live
  line status, and the account block at the bottom.
- **Center** — greeting, one large "Ask anything…" composer with
  mic and send, quick-action chips (Call WorkPal, Plan today,
  Draft a follow-up, Check my inbox), a status line, and two cards
  under it: Today (schedule) and Talk to WorkPal (the live line).
- **Right panel** — "Needs your approval": the review-queue draft
  with Approve / Hold, then Suggested jobs with Add buttons.

Mid widths: the right panel folds under the center column.

Phone widths: a real mobile app layout, not a squeezed page —
top app bar, the same content as views, and a bottom tab bar
(Home · Approvals with a badge · Call · Menu). The sidebar becomes
the Menu view; the right panel becomes the Approvals view.

## Sample vs real

Sample (from `examples/sample-workspace.json` and the foundation
demos, also used on the Nov 27, 2026 live test call):

- Business name "Sample Co", owner name "Demo Owner"
- 10:00 AM Acme Corp procurement sync, 1:30 PM Beta proposal
  scoping, tomorrow 9:00 AM Gamma Inc kickoff
- The review-queue item (follow-up email to John Tan at Acme Corp)
  and its draft wording
- The two suggested jobs

Real:

- The phone number. +1 240 231 6158 is the live WorkPal line;
  the Call chip, the tab-bar Call button, and the Call now button
  all dial it.
- The quiet hours (9:00 PM – 7:00 AM), the 8:00 AM morning call,
  and the read-back / approval rule are the system's real defaults
  and guardrails.

## Demo behaviour

This is a static design build. The Approve / Hold buttons record
the choice on screen and say plainly that nothing was sent; the
Add buttons toggle on screen; sending a message in the composer
echoes it with a canned WorkPal reply. Nothing is sent, saved, or
transmitted by this page. Wiring it to the live foundation (real
accounts, real queue, real approvals) is the next step.

v1 stays at `ui/morning-home/` for reference.

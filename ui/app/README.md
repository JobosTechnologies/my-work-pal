# My Work Pal — Owner App (v3, professional shell + in-app call + live themes)

The owner app for My Work Pal, redesigned to a professional standard
after v1 (ui/morning-home) was rejected as not professional enough.
The reference bar was MyCo's real app shell — this build works in
the same family (app shell, sidebar, chat center, side panel) but
is its own composition, not a copy: Sabi deep teal and amber on
dark teal-tinted neutrals, Fraunces display type for the greeting,
and a voice-first layout MyCo does not have (call panel, approval
queue up front).

New in v3 (owner feedback, Nov 27, 2026):

- **In-app call button.** The Talk to WorkPal panel leads with a
  press-to-talk button that connects to WorkPal inside the app,
  the way you talk to an assistant in-app. Calling by number
  (+1 240 231 6158) stays underneath as the second way in.
- **Live theme changing.** An Appearance section lets the owner
  change the whole look on the spot: Light / Dark / Match device,
  and four accent schemes (Sabi, Ocean, Grape, Ember). "The person
  can change the theme, they can change everything."

## Open it

No build step, no server. Just open the file in a browser:

```
ui/app/index.html
```

## Layout

Desktop (wide):

- **Left sidebar** — My Work Pal by JobOS Labs brand mark, nav
  (New, Jobs, Library, Client messages, Connected apps, Vault,
  Contacts), a Chats list (Morning brief, Get started), an
  Appearance section (theme + accent controls), the live line
  status, and the account block at the bottom.
- **Center** — greeting, one large "Ask anything…" composer with
  mic and send, quick-action chips (Call WorkPal, Plan today,
  Draft a follow-up, Check my inbox), a status line, and two cards
  under it: Today (schedule) and Talk to WorkPal (in-app call
  button first, the phone number second).
- **Right panel** — "Needs your approval": the review-queue draft
  with Approve / Hold, then Suggested jobs with Add buttons.

Mid widths: the right panel folds under the center column.

Phone widths: a real mobile app layout, not a squeezed page —
top app bar, the same content as views, and a bottom tab bar
(Home · Approvals with a badge · Call · Menu). The sidebar becomes
the Menu view (Appearance controls included); the right panel
becomes the Approvals view.

## Talk to WorkPal: two ways in

- **In-app call (primary).** One press on "Talk to WorkPal" and
  the owner is talking to WorkPal inside the app. In-app calls
  ride the internet, so they cost less than a phone call. The
  button runs a demo state cycle on this screen: Connecting… →
  In call with a running timer → End call → back to idle.
- **By number.** +1 240 231 6158, the real pilot line, sits right
  underneath for calling from any phone. And when the owner is
  away from the app entirely, WorkPal can call their phone
  instead — that is what the number is for.

## Appearance

Theme mode: **Light**, **Dark**, or **Match device** (follows the
phone or computer setting, and updates live if the device
switches). Accent schemes: **Sabi** (deep teal + amber, the
default), **Ocean** (blue + sky), **Grape** (plum + rose),
**Ember** (rust + gold). Every control restyles the whole app
immediately — surfaces, buttons, the call panel, the brand mark —
through CSS variables, and the choice is saved on the device
(localStorage) so it is still there next visit. The default is
the dark Sabi look.

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
  the Call chip, the tab-bar Call button, and the Call the number
  button all dial it.
- The quiet hours (9:00 PM – 7:00 AM), the 8:00 AM morning call,
  and the read-back / approval rule are the system's real defaults
  and guardrails.

## Demo behaviour

This is a static design build. The Approve / Hold buttons record
the choice on screen and say plainly that nothing was sent; the
Add buttons toggle on screen; sending a message in the composer
echoes it with a canned WorkPal reply. The in-app call button
cycles its on-screen states only — no audio is captured or
transmitted, and no real call is placed. Appearance choices are
the one thing the page does save, on the device only
(localStorage). Nothing else is sent, saved, or transmitted by
this page. Wiring it to the live foundation (real accounts, real
queue, real approvals, a real in-app call) is the next step.

v1 stays at `ui/morning-home/` for reference.

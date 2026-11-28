# My Work Pal — Morning Home (first owner screen)

The first screen an owner can actually see. It is the morning home:
greeting, today's schedule, the review queue, a "call WorkPal" panel,
and a quiet-hours / status line, in the settled Sabi palette
(deep teal, cream, amber). It follows the system light/dark setting
and works on phone and desktop widths.

## Open it

No build step, no server. Just open the file in a browser:

```
ui/morning-home/index.html
```

(Double-click it, or drag it into a browser window.)

## What is on it

- **Greeting hero** — "Good morning", the day's shape at a glance:
  meetings today, items waiting for approval, urgent email flagged.
- **Your schedule** — today and tomorrow, from the sample workspace.
- **Review queue** — one item waiting: a follow-up email to
  John Tan at Acme Corp, with the draft shown and Approve / Hold
  buttons. This is the heart of My Work Pal: nothing is sent
  without the owner's yes.
- **Talk to WorkPal** — the live pilot number, +1 240 231 6158.
  Tapping it on a phone dials WorkPal.
- **Your tools** — Gmail and Google Calendar shown as "connecting"
  (the Google wiring is in progress), phone line connected.
- **Status strip** — quiet hours 9:00 PM – 7:00 AM, morning call
  at 8:00 AM, approval rule restated.

## Sample vs real

Sample (from `examples/sample-workspace.json` and the foundation
demos, also used on the Nov 27, 2026 live test call):

- Business name "Sample Co", owner name "Demo Owner"
- 10:00 AM Acme Corp procurement sync, 1:30 PM Beta proposal
  scoping, tomorrow 9:00 AM Gamma Inc kickoff
- The review queue item and its draft wording

Real:

- The phone number. +1 240 231 6158 is the live WorkPal line;
  inbound calls are answered today.
- The quiet hours, morning call time, and the read-back /
  approval rule are the system's real defaults and guardrails.

Demo behaviour: the Approve and Hold buttons only record the
choice on screen. Nothing is sent, saved, or transmitted by this
static page. Wiring the screen to the live foundation (real
calendar, real queue, real approvals) is the next step.

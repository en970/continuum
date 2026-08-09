---
name: continuum
description: Makes long-running work survive session boundaries. Work carries on from where it stopped after a usage limit, a lost context, or a /clear. Use when the user says "use continuum", "work with continuum", "turn continuum on", "keep going even if the session ends", "don't stop at the limit", or "pick up where you left off". Also use when planning a task that will clearly span more than one session.
---

# Continuum

Frees a task from having to fit in one session. Two parts:

1. **State on disk** — `.continuum/PLAN.md` and `.continuum/STATE.md`. Even with
   no context left, these say what the work is and where it stopped.
2. **The watcher** — the `continuum` background process. When a session hits its
   usage limit, it types a resume message into that pane the moment the limit
   resets.

The watcher revives the session; the `.continuum` files are what let the revived
session know what to do. They work together.

## Turning it on (first request)

1. **Make sure the watcher is up.** Silent when it already is:

       continuum ensure

2. **Write `.continuum/PLAN.md`.** Mostly static for the life of the task:

       # Goal
       (one paragraph: what will be true when this is done)

       # Tasks
       - [ ] 1. ...
       - [ ] 2. ...

       # Definition of done
       (which command shows what, which tests pass)

       # Boundaries
       (files not to touch, dependencies not to add, decisions the user made)

3. **Write `.continuum/STATE.md`.** Updated often; the first thing a resumed
   session reads:

       # Now
       (which task, one sentence)

       # Next step
       (the concrete thing to do first — name the file and line)

       # Done
       - ...

       # Worth knowing
       (paths already tried and failed, environment quirks, assumptions made)

       # Last updated
       (date and time)

4. Tell the user in one line: which files were created, whether the watcher is up.

Do **not** add `.continuum/` to `.gitignore` — if the state travels with the
repo, the work continues on another machine too. Add it only if the user asks.

## While working

- Update `STATE.md` after each main step: a task item closing, or any stretch of
  work longer than 15-20 minutes. Not after every small edit — that is noise.
- Always record a path that was tried and failed under `Worth knowing`. It is
  what stops the resumed session walking into the same wall.
- Keep `Next step` filled in. Never leave it empty; write "Complete" when done.
- The watcher sometimes interrupts with a request to update `STATE.md`. That
  means the limit is close: write down where you are, then carry on.

## When resuming

A session that opens with something like `read .continuum/STATE.md and continue`:

1. Read `STATE.md`, then `PLAN.md`.
2. Do the work in `Next step`. Do not re-plan and do not ask the user what to do
   — the plan is already on disk.
3. Do not redo anything under `Done`. If unsure, check the files rather than
   asking.
4. Update `STATE.md` when the step is finished, then move to the next one.

## Customising the resume message

The default resume message is `continue`. In a directory containing
`.continuum/`, the watcher sends a message telling the session to read STATE.md
instead. For a project-specific message, add a path substring and text to
`resume_text_overrides` in the watcher's `config.json`.

## When asked about status

    continuum status    # which session is at a limit, when it resumes
    continuum log       # what was sent and when

`.continuum/STATE.md` says where the work is; `continuum status` says whether the
session is alive. Those are different questions.

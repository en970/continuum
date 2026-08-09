# Continuum

Keep long-running Claude Code sessions going across usage limits.

You leave a session working on something. It hits your usage limit, stops, and
waits. Hours later the limit resets — and the session is still sitting there,
because nobody was around to type anything. Continuum types it for you, once,
at the moment the limit resets.

**This does not bypass, extend, or work around any limit.** It waits for the
limit you already have to reset, then sends a single message you would have
sent yourself. Nothing is sent while a limit is in effect.

---

## What it does

- Watches your terminal panes for the usage-limit message.
- Reads the reset time out of that message (`resets 1:50am`, `try again in 2h
  15m`, weekly limits, and so on).
- At that time, sends **one** resume message to that pane. If it does not take,
  one backup attempt five minutes later — then it stops and tells you.
- Optionally keeps a plan and a progress file in the project, so a session that
  lost its context still knows what it was doing.
- A small macOS app lets you tick which sessions may be resumed.

Works with **tmux** on any OS, and with **Terminal.app** on macOS. tmux is
preferred when present: no permissions needed, and nothing to break.

## Install

```sh
git clone https://github.com/YOURNAME/continuum.git
cd continuum
./install.sh
```

That links the `continuum` command, installs the Claude Code skill, adds a small
shell hook that revives the watcher if it ever dies, builds the macOS app, and
starts watching. Python 3.8+ and no third-party packages.

Remove everything with `./install.sh --uninstall`.

## Use

Nothing is required day to day — the watcher runs in the background.

```sh
continuum status    # health, plus every watched session
continuum scan      # what is detected right now (sends nothing)
continuum log       # what was sent and when
continuum stop      # stop the watcher
continuum start     # start it again
```

By default **only projects with a `.continuum` plan are resumed** (see below).
Open the app — Spotlight, "Continuum" — to tick any other session. Choices are
stored per project path, so reopening the same project in a new tab keeps them.

## Surviving a lost context

Reviving a session is only half the problem. If its context was compacted or
cleared, the session wakes up with no idea what it was doing.

Say **"use continuum"** in a session and Claude will write two files into the
project:

```
.continuum/PLAN.md    the goal, the task list, what "done" means
.continuum/STATE.md   where it is now, the next concrete step, what it learned
```

`STATE.md` is updated as the work proceeds. In a project with these files, the
resume message becomes *read `.continuum/STATE.md` and carry on from the "Next
step" section* — so the work continues even with an empty context.

When a limit is close (`Approaching usage limit`, or context dropping below
15%), the watcher asks the session to save its progress first. That way a limit
landing mid-task does not lose the thread. At most once every 30 minutes per
session.

## Configuration

`config.json` next to `continuum.py`. Every key is optional; defaults are in
`DEFAULT_CONFIG` at the top of the script.

| Key | Default | What it does |
|---|---|---|
| `target_processes` | `["claude","codex"]` | Which CLIs count as a session |
| `backends` | auto | `["tmux"]`, `["terminal_app"]`, or both |
| `resume_text` | `continue` | Sent in ordinary projects |
| `continuum_resume_text` | read STATE.md… | Sent in `.continuum` projects |
| `resume_text_overrides` | `{}` | Path substring → custom message |
| `poll_interval` | `30` | Seconds between scans |
| `max_attempts` | `2` | One send at reset, one backup |
| `checkpoint_enabled` | `true` | Ask for progress saves before the limit |
| `prevent_sleep` | `always` | `never`, `when_waiting`, `always` |
| `dry_run` | `false` | Detect and log, never type |
| `limit_patterns` | see below | Regexes that mean "at the limit" |

**Detection patterns live in config on purpose.** The vendor's wording changes
and detection then fails silently — this project has already been bitten once,
when `You've hit your session limit` did not match patterns written for
`usage limit reached`. If Continuum stops noticing your limits, run
`continuum scan`, look at the real message, and add a pattern. No release
needed.

## Not typing into the wrong pane

- A message is only ever sent to a pane where a limit message was detected.
- Nothing is sent while the session is working (`esc to interrupt` on screen).
- The target pane is resolved again at send time — Terminal.app window indexes
  shift, so an index captured earlier cannot be trusted.
- At least four minutes between sends to the same pane; at most two attempts,
  then it gives up and notifies.
- If you resume a session by hand, the watcher notices and resets its state.
- `"dry_run": true` logs what it would do and types nothing.

## Sleep

If the machine sleeps, neither the watching nor the resuming happens. While
sessions are being watched the watcher holds off system sleep (`caffeinate -s`
on macOS — the display still switches off). Set `prevent_sleep` to
`when_waiting` to hold it only while a limit is pending, or `never` to leave
sleep alone.

## How the watcher stays alive

It runs detached in its own session, so closing the window it was started from
does not kill it. If it dies anyway, the shell hook added at install brings it
back the next time you open a terminal, and `continuum status` says plainly
whether it is running and when it last scanned.

It is deliberately **not** a launchd job on macOS: a launchd process needs its
own Automation permission to drive Terminal.app, and that dialog cannot be
approved while you are away — the process just hangs. Started from a terminal,
the permission is inherited. This does not apply to tmux, which needs no
permissions at all.

## Troubleshooting

**Limits are not detected.** Run `continuum scan` while a session sits at a
limit. If it shows `idle` rather than `AT LIMIT`, the wording changed — copy the
real line into `limit_patterns`.

**Nothing is sent.** Check the tick in the app, or `continuum status`: an
unticked session shows `[ ]` and is left alone by design.

**"Watcher not running".** `continuum start`. If it will not stay up, run
`continuum watch` in the foreground and read the error.

**Terminal.app permission.** The first run asks for permission to control
Terminal. Approve it once. Using tmux avoids this entirely.

## Layout

```
continuum.py        watcher and CLI
config.json         optional, overrides the defaults
install.sh          installer / uninstaller
skill/SKILL.md      Claude Code skill ("use continuum")
app/                SwiftUI menu app, icon generator, build script
```

State lives in `~/.local/state/continuum/` (or `$XDG_STATE_HOME`): `state.json`,
your ticks in `enabled.json`, and `continuum.log`.

## Licence

MIT. See [LICENSE](LICENSE).

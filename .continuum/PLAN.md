# Goal

Continuum is published and works: sessions that stop at a usage limit resume
by themselves when the limit resets, and they resume knowing what they were
doing. Repo: https://github.com/en970/continuum (public, MIT).

# Tasks

- [x] 1. Watcher that detects a limit and resumes the session
- [x] 2. tmux backend alongside macOS Terminal.app
- [x] 3. Detection patterns in config rather than hardcoded
- [x] 4. No hardcoded paths; one-command installer
- [x] 5. Everything in English; README and LICENSE
- [x] 6. SwiftUI picker app with an icon
- [x] 7. Claude Code hooks: StopFailure, SessionStart, PreCompact
- [x] 8. README screenshot using example data
- [ ] 9. Windows/WSL support (currently tmux or Terminal.app only)
- [ ] 10. Decide whether to announce it anywhere

# Definition of done

`continuum status` shows a healthy watcher and the open sessions; a session
that hits a limit resumes on its own at the reset time; a session opened in a
project with a `.continuum` plan already knows the plan without being told.

# Boundaries

- Never resume while a limit is still in effect. This is not a limit bypass.
- One send at the reset time, then a single backup attempt. No repeated typing
  into panes — the user asked for this explicitly.
- Only sessions ticked in the app (or carrying a `.continuum` plan) are touched.
- Hooks merged into `~/.claude/settings.json` must leave existing hooks alone.

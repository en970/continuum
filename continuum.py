#!/usr/bin/env python3
"""
Continuum — keep long-running CLI agent sessions going across usage limits.

When a Claude Code (or Codex) session hits its usage limit it stops and waits.
Continuum notices, reads the reset time out of the limit message, and at that
moment sends the session a single message telling it to carry on.

It does not bypass or extend any limit. It waits for the limit you already have
to reset, then types what you would have typed yourself.

Two layers:

  * This watcher      — revives the session when the limit resets.
  * .continuum/ files — PLAN.md and STATE.md in the project, so the revived
                        session knows what it was doing even if its context
                        was lost.

Supported panes: tmux (any OS) and macOS Terminal.app. tmux is preferred when
available: it needs no accessibility permissions and survives anything.

Standard library only.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

VERSION = "1.0.0"

# --- Paths ----------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("CONTINUUM_CONFIG", BASE_DIR / "config.json"))

_xdg_state = os.environ.get("XDG_STATE_HOME")
STATE_DIR = Path(_xdg_state) / "continuum" if _xdg_state else Path.home() / ".local" / "state" / "continuum"
STATE_PATH = STATE_DIR / "state.json"
ENABLED_PATH = STATE_DIR / "enabled.json"
LOG_PATH = STATE_DIR / "continuum.log"
PID_PATH = STATE_DIR / "continuum.pid"
# The GUI reads this to find the CLI regardless of where the repo was cloned.
INSTALL_PATH = STATE_DIR / "install.json"

log = logging.getLogger("continuum")


# --- Default configuration ------------------------------------------------

DEFAULT_CONFIG = {
    # Which CLI processes count as an agent session
    "target_processes": ["claude", "codex"],
    # Which pane sources to scan: "tmux", "terminal_app". Empty = auto-detect.
    "backends": [],

    # What to send when the limit resets
    "resume_text": "continue",
    # Used instead when the project has a .continuum/STATE.md
    "continuum_resume_text": (
        "Read .continuum/STATE.md and carry on from the 'Next step' section. "
        "Update STATE.md when the step is done."
    ),
    # Per-project override: any path substring -> text
    "resume_text_overrides": {},

    # Timing
    "poll_interval": 30,
    "grace_seconds": 45,
    # Used when the message has no readable reset time
    "fallback_retry_minutes": 20,
    # One send at the reset time, then a single backup attempt
    "retry_backoff_minutes": [5],
    "max_attempts": 2,
    "min_send_interval": 240,
    "verify_after_seconds": 90,

    # Ask the session to save progress before the limit lands
    "checkpoint_enabled": True,
    "checkpoint_interval": 1800,
    "checkpoint_text": (
        "Update .continuum/STATE.md with the current progress. "
        "Only update that file, do not start other work."
    ),

    # Behaviour
    "notifications": True,
    "dry_run": False,
    "ignore_paths": [],
    # never | when_waiting | always. The machine sleeping stops everything.
    "prevent_sleep": "always",

    # --- Detection patterns -------------------------------------------------
    # Wording changes on the vendor's side break detection silently, so these
    # live in config: you can fix them without waiting for a release.
    # Real Claude Code message, for reference:
    #   You've hit your session limit · resets 1:50am (Europe/Istanbul)
    "limit_patterns": [
        r"you'?ve (hit|reached) your .{0,25}limit",
        r"(session|usage|weekly|monthly|daily) limit reached",
        r"\d+\s*-?\s*hour limit reached",
        r"limit\s*[·:.-]\s*resets",
        r"limit will reset at",
        r"out of (weekly |monthly )?(usage|credits)",
        r"rate limit exceeded.*try again",
    ],
    # Lines that mention a limit but are not one. The "/upgrade to increase
    # your usage limit." line sits right under the real message.
    "warning_patterns": [
        r"/upgrade to increase",
        r"to increase your .{0,20}limit",
        r"approaching .{0,30}limit",
        r"\d+%\s*of .{0,20}limit",
        r"limit.{0,20}remaining",
    ],
    # The session is working right now — never type into it
    "busy_patterns": [
        r"esc to interrupt",
        r"ctrl\+c to (stop|interrupt)",
    ],
    # Limit or context is about to run out — ask for a checkpoint
    "checkpoint_patterns": [
        r"approaching .{0,30}limit",
        r"context left until auto-compact:\s*(?:[0-9]|1[0-5])%",
        r"\d+%\s*of .{0,20}limit .{0,10}used",
    ],
}


# --- Reset time parsing ---------------------------------------------------

RESET_CLOCK = re.compile(
    r"reset[s]?(?:\s+(?:at|on))?\s+"
    r"(?:(?P<mon>[a-z]{3,9})\s+(?P<day>\d{1,2})\s+(?:at\s+)?)?"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?",
    re.IGNORECASE,
)
RESET_RELATIVE = re.compile(
    r"(?:try again in|resets? in|available in)\s+"
    r"(?:(?P<h>\d+)\s*h(?:ours?|rs?)?)?\s*"
    r"(?:(?P<m>\d+)\s*m(?:in(?:utes?)?)?)?",
    re.IGNORECASE,
)
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_reset_time(message, now=None):
    """Extract the reset moment from a limit message. Returns epoch or None."""
    now = now or datetime.now()

    rel = RESET_RELATIVE.search(message)
    if rel and (rel.group("h") or rel.group("m")):
        delta = timedelta(hours=int(rel.group("h") or 0),
                          minutes=int(rel.group("m") or 0))
        if delta.total_seconds() > 0:
            return (now + delta).timestamp()

    clock = RESET_CLOCK.search(message)
    if not clock:
        return None

    hour = int(clock.group("hour"))
    minute = int(clock.group("minute") or 0)
    ampm = (clock.group("ampm") or "").lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    mon, day = clock.group("mon"), clock.group("day")
    if mon and day and mon[:3].lower() in MONTHS:
        month = MONTHS[mon[:3].lower()]
        year = now.year if (month, int(day)) >= (now.month, now.day) else now.year + 1
        try:
            target = target.replace(year=year, month=month, day=int(day))
        except ValueError:
            return None
    elif target <= now:
        # Limit windows are short. A time in the past means "it just reset",
        # not "tomorrow" — so resume now. Only treat it as stale, and roll to
        # the next day, if it is more than 12 hours behind.
        if (now - target).total_seconds() > 12 * 3600:
            target += timedelta(days=1)

    return target.timestamp()


# --- Pane backends --------------------------------------------------------

class Pane:
    """One terminal pane holding (possibly) an agent session."""

    def __init__(self, key, backend, target, cwd, text, label, procs=""):
        self.key = key          # stable id used in state, e.g. "tmux:%4"
        self.backend = backend
        self.target = target    # backend-specific address for sending
        self.cwd = cwd or ""
        self.text = text or ""
        self.label = label
        self.procs = procs.lower()

    def runs_any(self, names):
        hay = self.procs
        return any(n in hay for n in names)


def _run(cmd, timeout=20, stdin_text=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, input=stdin_text)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        log.debug("command failed %s: %s", cmd[:2], exc)
        return None
    if p.returncode != 0:
        log.debug("command returned %d: %s", p.returncode, p.stderr[:200])
        return None
    return p.stdout


def _descendant_commands(pid, depth=3):
    """Command names of a process and its children — used to spot 'claude'."""
    names = []
    frontier = [str(pid)]
    for _ in range(depth):
        if not frontier:
            break
        out = _run(["ps", "-o", "pid=,comm=", "-p", ",".join(frontier)], timeout=8)
        if out:
            for line in out.splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2:
                    names.append(os.path.basename(parts[1]).lower())
        children = []
        for p in frontier:
            kids = _run(["pgrep", "-P", p], timeout=8)
            if kids:
                children.extend(kids.split())
        frontier = children
    return " ".join(names)


class TmuxBackend:
    """Panes inside tmux. No OS permissions needed; works on macOS and Linux."""

    name = "tmux"

    @staticmethod
    def available():
        if not shutil.which("tmux"):
            return False
        # A server must actually be running
        return _run(["tmux", "list-sessions"], timeout=8) is not None

    def panes(self):
        fmt = "#{pane_id}\t#{pane_current_path}\t#{pane_current_command}\t#{pane_pid}\t#{session_name}:#{window_index}.#{pane_index}"
        out = _run(["tmux", "list-panes", "-a", "-F", fmt])
        if not out:
            return []
        panes = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            pane_id, cwd, cmd, pane_pid, label = parts[:5]
            text = _run(["tmux", "capture-pane", "-p", "-t", pane_id, "-S", "-80"]) or ""
            procs = cmd
            if not any(t in cmd.lower() for t in ("claude", "codex")):
                procs = cmd + " " + _descendant_commands(pane_pid)
            panes.append(Pane(key="tmux:" + pane_id, backend=self.name,
                              target=pane_id, cwd=cwd, text=text,
                              label=label, procs=procs))
        return panes

    def send(self, target, text):
        # -l sends the string literally, so quotes and slashes survive intact
        if _run(["tmux", "send-keys", "-t", target, "-l", text]) is None:
            return False
        return _run(["tmux", "send-keys", "-t", target, "Enter"]) is not None


TERMINAL_APP_READ = """
tell application "Terminal"
  set fs to (ASCII character 30)
  set rs to (ASCII character 29)
  set out to ""
  repeat with w from 1 to (count windows)
    try
      repeat with t from 1 to (count tabs of window w)
        try
          -- Reading via a variable makes "contents of" fall back to
          -- AppleScript's dereference operator, so address the tab directly.
          set tt to (tty of tab t of window w)
          set procs to ""
          try
            set procs to ((processes of tab t of window w) as string)
          end try
          set body to ""
          try
            set body to (get contents of tab t of window w)
          end try
          set out to out & tt & fs & procs & fs & body & rs
        end try
      end repeat
    end try
  end repeat
  return out
end tell
"""


class TerminalAppBackend:
    """macOS Terminal.app tabs, read over AppleScript."""

    name = "terminal_app"
    FS, RS = "\x1e", "\x1d"

    @staticmethod
    def available():
        if sys.platform != "darwin" or not shutil.which("osascript"):
            return False
        return _run(["osascript", "-e",
                     'tell application "System Events" to (name of processes) contains "Terminal"'],
                    timeout=10) is not None

    def panes(self):
        out = _run(["osascript", "-"], timeout=30, stdin_text=TERMINAL_APP_READ)
        if not out:
            return []
        panes = []
        for record in out.split(self.RS):
            if not record.strip():
                continue
            parts = record.split(self.FS)
            if len(parts) < 3:
                continue
            tty, procs, text = parts[0].strip(), parts[1], parts[2]
            if not tty:
                continue
            panes.append(Pane(key="term:" + tty, backend=self.name, target=tty,
                              cwd=self._cwd_for(tty), text=text,
                              label=tty.replace("/dev/", ""), procs=procs))
        return panes

    @staticmethod
    def _cwd_for(tty_path):
        short = tty_path.replace("/dev/", "")
        out = _run(["ps", "-Ao", "pid=,tty=,comm="], timeout=10)
        if not out:
            return ""
        for line in out.splitlines():
            cols = line.split(None, 2)
            if len(cols) < 3:
                continue
            pid, tty_col, comm = cols
            if tty_col != short or "login" in comm or comm.endswith("sh"):
                continue
            lsof = _run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], timeout=10)
            for l in (lsof or "").splitlines():
                if l.startswith("n/"):
                    return l[1:]
        return ""

    def send(self, target, text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        # Window indexes shift, so the tab is located by tty at send time
        script = f'''
tell application "Terminal"
  set sent to "no"
  repeat with w from 1 to (count windows)
    try
      repeat with t from 1 to (count tabs of window w)
        try
          if (tty of tab t of window w) is "{target}" then
            do script "{escaped}" in tab t of window w
            set sent to "yes"
          end if
        end try
      end repeat
    end try
  end repeat
  return sent
end tell
'''
        out = _run(["osascript", "-"], timeout=25, stdin_text=script)
        return bool(out and out.strip() == "yes")


ALL_BACKENDS = {"tmux": TmuxBackend, "terminal_app": TerminalAppBackend}


def active_backends(cfg):
    wanted = cfg.get("backends") or list(ALL_BACKENDS)
    out = []
    for name in wanted:
        cls = ALL_BACKENDS.get(name)
        if cls and cls.available():
            out.append(cls())
    return out


# --- Screen text analysis -------------------------------------------------

def tail_text(text, lines=40):
    """
    Last meaningful lines of a pane. Trailing blank lines are dropped first:
    otherwise an empty lower half pushes the limit line out of the window.
    """
    rows = text.splitlines()
    while rows and not rows[-1].strip():
        rows.pop()
    return "\n".join(rows[-lines:])


def _matches(patterns, text):
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def is_busy(cfg, text):
    return _matches(cfg["busy_patterns"], tail_text(text, 12))


def find_limit(cfg, text, lines=40):
    """Return the limit line if the pane is sitting at a usage limit."""
    for line in reversed(tail_text(text, lines).splitlines()):
        clean = line.strip()
        if not clean or _matches(cfg["warning_patterns"], clean):
            continue
        if _matches(cfg["limit_patterns"], clean):
            return clean
    return None


def needs_checkpoint(cfg, text):
    return _matches(cfg["checkpoint_patterns"], tail_text(text, 20))


# --- Project state --------------------------------------------------------

def has_continuum(cwd):
    return bool(cwd) and (Path(cwd) / ".continuum" / "STATE.md").exists()


def resume_text_for(cfg, cwd):
    """Explicit override > .continuum project > plain default."""
    if cwd:
        for key, text in (cfg.get("resume_text_overrides") or {}).items():
            if key.lower() in cwd.lower():
                return text
        if has_continuum(cwd):
            return cfg["continuum_resume_text"]
    return cfg["resume_text"]


def is_enabled(cwd, enabled):
    """
    Should this session be resumed? An explicit choice in the app wins;
    without one, projects carrying a .continuum plan default to on.
    """
    if cwd in enabled:
        return bool(enabled[cwd])
    return has_continuum(cwd)


# --- Small helpers --------------------------------------------------------

def setup_logging(verbose=False):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3)
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as exc:
            log.error("could not read %s (%s); using defaults", CONFIG_PATH, exc)
    return cfg


def _load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("%s is unreadable, ignoring it", path.name)
    return default


def load_state():
    return _load_json(STATE_PATH, {})


def load_enabled():
    return _load_json(ENABLED_PATH, {})


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_PATH)


def write_install_marker():
    """Let the GUI find this script wherever the repo lives."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    INSTALL_PATH.write_text(json.dumps({
        "script": str(BASE_DIR / "continuum.py"),
        "python": sys.executable,
        "version": VERSION,
    }, indent=2), encoding="utf-8")


def notify(title, message, enabled=True):
    if not enabled or sys.platform != "darwin":
        return
    safe = lambda s: s.replace('"', "'")
    subprocess.run(["osascript", "-e",
                    f'display notification "{safe(message)}" with title "{safe(title)}"'],
                   capture_output=True)


_caffeinate = None


def keep_awake(needed):
    """
    Hold off system sleep while sessions are being watched. If the machine
    sleeps, neither the watching nor the resuming happens. -s leaves the
    display free to switch off.
    """
    global _caffeinate
    if sys.platform != "darwin":
        return
    alive = _caffeinate is not None and _caffeinate.poll() is None
    if needed and not alive:
        _caffeinate = subprocess.Popen(
            ["/usr/bin/caffeinate", "-s", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log.info("holding off sleep while sessions are watched")
    elif not needed and alive:
        _caffeinate.terminate()
        _caffeinate = None
        log.info("released sleep hold")


def daemon_pid():
    if not PID_PATH.exists():
        return None
    try:
        pid = int(PID_PATH.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        return None


# --- Core loop ------------------------------------------------------------

def scan_once(cfg, state, backends):
    now = time.time()
    enabled = load_enabled()
    seen = set()

    for backend in backends:
        for pane in backend.panes():
            if not pane.runs_any(cfg["target_processes"]):
                continue
            seen.add(pane.key)

            entry = state.setdefault(pane.key, {
                "status": "active", "attempts": 0, "last_send": 0,
                "reset_at": None, "cwd": None, "message": None,
            })
            entry["last_seen"] = now
            entry["backend"] = pane.backend
            entry["label"] = pane.label
            if pane.cwd:
                entry["cwd"] = pane.cwd
            cwd = entry.get("cwd") or ""

            if any(k.lower() in cwd.lower() for k in cfg.get("ignore_paths", [])):
                entry["status"] = "ignored"
                continue

            busy = is_busy(cfg, pane.text)
            limit_msg = find_limit(cfg, pane.text)

            # --- verify a send that already went out ---
            if entry["status"] == "probing":
                if now < entry.get("verify_at", 0):
                    continue
                # Narrow window: if the limit line scrolled up, it resumed
                if busy or not find_limit(cfg, pane.text, lines=15):
                    log.info("%s resumed (%s)", pane.label, cwd or "?")
                    notify("Session resumed",
                           f"{os.path.basename(cwd) or pane.label} is running again",
                           cfg["notifications"])
                    entry.update(status="active", attempts=0, reset_at=None, message=None)
                else:
                    idx = min(max(entry["attempts"] - 1, 0),
                              len(cfg["retry_backoff_minutes"]) - 1)
                    wait = cfg["retry_backoff_minutes"][idx]
                    entry.update(status="waiting", reset_at=now + wait * 60)
                    log.info("%s not reset yet, retrying in %d min (attempt %d)",
                             pane.label, wait, entry["attempts"])
                continue

            # --- detect the limit ---
            if limit_msg and not busy:
                if entry["status"] != "waiting":
                    reset_at = parse_reset_time(limit_msg)
                    if reset_at is None:
                        reset_at = now + cfg["fallback_retry_minutes"] * 60
                        when = "no time in message, retrying in %d min" % cfg["fallback_retry_minutes"]
                    else:
                        when = datetime.fromtimestamp(reset_at).strftime("%d %b %H:%M")
                    entry.update(status="waiting", reset_at=reset_at, message=limit_msg)
                    log.info("LIMIT %s (%s) -> %s | %s",
                             pane.label, cwd or "?", when, limit_msg[:110])
                    notify("Usage limit reached",
                           f"{os.path.basename(cwd) or pane.label} waiting, resumes {when}",
                           cfg["notifications"])
            elif busy and entry["status"] == "waiting":
                log.info("%s was resumed by hand, clearing", pane.label)
                entry.update(status="active", attempts=0, reset_at=None, message=None)

            # --- ask for a checkpoint before the limit lands ---
            if (cfg["checkpoint_enabled"] and not limit_msg and not busy
                    and has_continuum(cwd) and needs_checkpoint(cfg, pane.text)
                    and now - entry.get("last_checkpoint", 0) > cfg["checkpoint_interval"]
                    and not cfg["dry_run"]):
                if backend.send(pane.target, cfg["checkpoint_text"]):
                    entry["last_checkpoint"] = now
                    log.info("checkpoint requested from %s (%s)", pane.label, cwd or "?")

            # --- resume ---
            if entry["status"] == "waiting" and entry.get("reset_at"):
                if not is_enabled(cwd, enabled):
                    if not entry.get("skip_logged"):
                        log.info("%s (%s) is not selected, leaving it alone",
                                 pane.label, os.path.basename(cwd) or "?")
                        entry["skip_logged"] = True
                    continue
                entry.pop("skip_logged", None)

                if now < entry["reset_at"] + cfg["grace_seconds"] or busy:
                    continue
                if now - entry.get("last_send", 0) < cfg["min_send_interval"]:
                    continue
                if entry["attempts"] >= cfg["max_attempts"]:
                    if entry["status"] != "gave_up":
                        log.warning("%s: attempt limit reached, giving up", pane.label)
                        notify("Could not resume",
                               f"{os.path.basename(cwd) or pane.label} needs a hand",
                               cfg["notifications"])
                        entry["status"] = "gave_up"
                    continue

                text = resume_text_for(cfg, cwd)
                if cfg["dry_run"]:
                    log.info("[dry-run] would send %r to %s", text, pane.label)
                    entry.update(status="active", reset_at=None)
                    continue
                if backend.send(pane.target, text):
                    entry.update(attempts=entry["attempts"] + 1, last_send=now,
                                 status="probing", verify_at=now + cfg["verify_after_seconds"])
                    log.info("SENT %r -> %s (%s, attempt %d)",
                             text, pane.label, cwd or "?", entry["attempts"])
                else:
                    log.error("could not write to %s (pane may be gone)", pane.label)
                    entry["status"] = "lost"

    for key in list(state):
        if key not in seen and now - state[key].get("last_seen", 0) > 3600:
            del state[key]

    policy = cfg.get("prevent_sleep", "always")
    if policy == "never":
        keep_awake(False)
    elif policy == "when_waiting":
        keep_awake(any(e.get("status") in ("waiting", "probing") for e in state.values()))
    else:
        keep_awake(bool(seen))


def cmd_watch(cfg, once=False):
    if not once:
        other = daemon_pid()
        if other and other != os.getpid():
            log.info("another watcher is running (pid %d), exiting", other)
            return
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        PID_PATH.write_text(str(os.getpid()), encoding="utf-8")

    write_install_marker()
    backends = active_backends(cfg)
    if not backends:
        log.error("no usable pane source found (tried tmux and Terminal.app)")
        return

    log.info("continuum %s started | panes=%s | targets=%s | every %ds%s",
             VERSION, ",".join(b.name for b in backends),
             ",".join(cfg["target_processes"]), cfg["poll_interval"],
             " | DRY RUN" if cfg["dry_run"] else "")

    state = load_state()
    while True:
        try:
            scan_once(cfg, state, backends)
            save_state(state)
        except Exception:
            log.exception("unexpected error during scan")
        if once:
            return
        time.sleep(cfg["poll_interval"])


# --- Commands -------------------------------------------------------------

def cmd_start(cfg, quiet=False):
    """
    Start the watcher detached from any terminal window.

    start_new_session makes it a session leader, so closing the window it was
    launched from does not send it SIGHUP. (An earlier version lived inside a
    Terminal window and died silently when that window was closed.)

    Not launchd: a launchd job needs its own Automation permission to drive
    Terminal.app, and that dialog cannot be approved while you are away.
    Started from a terminal, the permission is inherited.
    """
    if daemon_pid():
        if not quiet:
            print(f"Already running (pid {daemon_pid()}). Try: continuum status")
        return 0
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out = open(STATE_DIR / "daemon.out", "a")
    proc = subprocess.Popen([sys.executable, str(BASE_DIR / "continuum.py"), "watch"],
                            stdout=out, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True,
                            cwd=str(BASE_DIR))
    PID_PATH.write_text(str(proc.pid), encoding="utf-8")
    for _ in range(20):
        time.sleep(0.5)
        if daemon_pid() and LOG_PATH.exists():
            if not quiet:
                print(f"Watcher started in the background (pid {proc.pid}).")
            return 0
    if not quiet:
        print("Could not start; see: continuum log")
    return 1


def cmd_stop():
    pid = daemon_pid()
    if not pid:
        print("No watcher running.")
        return 0
    os.kill(pid, 15)
    try:
        PID_PATH.unlink()
    except OSError:
        pass
    print(f"Watcher stopped (pid {pid}).")
    return 0


def print_health(cfg):
    pid = daemon_pid()
    if not pid:
        print("WATCHER NOT RUNNING — start it with: continuum start\n")
        return False
    age = time.time() - STATE_PATH.stat().st_mtime if STATE_PATH.exists() else None
    if age is None:
        print(f"Watcher running (pid {pid}), first scan pending.\n")
    elif age > max(cfg["poll_interval"] * 4, 180):
        print(f"WARNING: watcher (pid {pid}) has not scanned for {int(age // 60)} min. "
              f"Try: continuum stop && continuum start\n")
    else:
        print(f"Watcher running (pid {pid}), last scan {int(age)}s ago.\n")
    return True


LABELS = {
    "active": "running", "waiting": "at limit", "probing": "resume sent",
    "gave_up": "gave up", "lost": "pane gone", "ignored": "ignored",
}


def cmd_status(cfg):
    print_health(cfg)
    state = load_state()
    if not state:
        print("No sessions being watched yet.")
        return
    enabled = load_enabled()
    now = time.time()
    print(f"{'ON':<4}{'PANE':<12}{'STATUS':<14}{'RESUMES':<16}{'TRY':<5}PROJECT")
    print("-" * 88)
    for key, e in sorted(state.items(), key=lambda kv: kv[1].get("label", "")):
        cwd = e.get("cwd") or ""
        mark = "[x]" if is_enabled(cwd, enabled) else "[ ]"
        when = "-"
        if e.get("reset_at"):
            left = e["reset_at"] - now
            stamp = datetime.fromtimestamp(e["reset_at"]).strftime("%H:%M")
            when = stamp if left <= 0 else f"{stamp} (+{int(left // 60)}m)"
        print(f"{mark:<4}{e.get('label', key)[:11]:<12}"
              f"{LABELS.get(e['status'], e['status']):<14}{when:<16}"
              f"{e.get('attempts', 0):<5}{os.path.basename(cwd) or '?'}")
        if e.get("message") and e["status"] in ("waiting", "gave_up"):
            print(f"{'':<4}  {e['message'][:76]}")
    print(f"\nLog: {LOG_PATH}")


def cmd_scan(cfg):
    """One pass, printing what would be detected. Sends nothing."""
    backends = active_backends(cfg)
    print("pane sources: " + (", ".join(b.name for b in backends) or "none found"))
    for backend in backends:
        for pane in backend.panes():
            if not pane.runs_any(cfg["target_processes"]):
                continue
            limit = find_limit(cfg, pane.text)
            state = "AT LIMIT" if limit else ("running" if is_busy(cfg, pane.text) else "idle")
            print(f"[{state:<9}] {pane.label:<14} {pane.cwd or '?'}")
            if limit:
                ts = parse_reset_time(limit)
                when = datetime.fromtimestamp(ts).strftime("%d %b %H:%M") if ts else "unreadable"
                print(f"{'':<12} message: {limit[:88]}")
                print(f"{'':<12} resumes: {when}")
                print(f"{'':<12} sends  : {resume_text_for(cfg, pane.cwd)[:70]}")


USAGE = f"""continuum {VERSION} — keep agent sessions going across usage limits

  continuum start     Start the watcher in the background
  continuum stop      Stop the watcher
  continuum status    Health plus every watched session
  continuum scan      Show what is detected right now (sends nothing)
  continuum watch     Run in the foreground (Ctrl+C to quit)
  continuum once      Single pass
  continuum ensure    Start only if not already running (used by the shell hook)
  continuum log       Recent log lines

Config: {CONFIG_PATH}
State : {STATE_DIR}
"""


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    if cmd in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if cmd in ("-V", "--version"):
        print(VERSION)
        return 0

    setup_logging("-v" in args or "--verbose" in args)
    cfg = load_config()

    if cmd == "watch":
        cmd_watch(cfg)
    elif cmd == "once":
        cmd_watch(cfg, once=True)
    elif cmd == "start":
        return cmd_start(cfg)
    elif cmd == "ensure":
        return 0 if daemon_pid() else cmd_start(cfg, quiet=True)
    elif cmd == "stop":
        return cmd_stop()
    elif cmd == "status":
        cmd_status(cfg)
    elif cmd == "scan":
        cmd_scan(cfg)
    elif cmd == "log":
        if LOG_PATH.exists():
            print("".join(LOG_PATH.read_text(encoding="utf-8").splitlines(True)[-40:]))
        else:
            print("No log yet.")
    else:
        print(USAGE)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped.")

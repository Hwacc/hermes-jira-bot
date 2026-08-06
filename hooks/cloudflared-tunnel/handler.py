"""Hermes hook: start cloudflared tunnel on gateway startup.

Requires cloudflared CLI on PATH and a named tunnel configured.
Environment variables (optional):
  CLOUDFLARED_TUNNEL       — tunnel name (default: hermes-jira-webhook)
  CLOUDFLARED_CONFIG       — path to config.yml (default: ~/.cloudflared/config.yml)
  CLOUDFLARED_PID_FILE     — pid file path (default: $HERMES_HOME/cloudflared-tunnel.pid)
  CLOUDFLARED_STARTUP_GRACE — seconds to wait before declaring success (default: 2)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_TUNNEL = "hermes-jira-webhook"
TUNNEL = os.environ.get("CLOUDFLARED_TUNNEL", DEFAULT_TUNNEL)
CONFIG = os.environ.get(
    "CLOUDFLARED_CONFIG",
    str(Path.home() / ".cloudflared" / "config.yml"),
)
_HERMES_HOME = Path(
    os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")
)
PID_FILE = Path(
    os.environ.get(
        "CLOUDFLARED_PID_FILE",
        str(_HERMES_HOME / "cloudflared-tunnel.pid"),
    )
)
LOG_FILE = PID_FILE.with_suffix(".log")
STARTUP_GRACE_SEC = float(os.environ.get("CLOUDFLARED_STARTUP_GRACE", "2"))


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid() -> int | None:
    try:
        if PID_FILE.is_file():
            return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        pass
    return None


def _write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="utf-8")


def _clear_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _log_tail(max_bytes: int = 800) -> str:
    try:
        data = LOG_FILE.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _popen_kwargs() -> dict:
    """Detach so a brief gateway recycle does not instantly kill the tunnel."""
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
        return {"creationflags": 0x00000200 | 0x00000008}
    return {"start_new_session": True}


async def handle(event_type, context):
    """Start cloudflared tunnel when gateway starts (idempotent)."""
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        print(
            "[cloudflared-tunnel] cloudflared not found on PATH, tunnel not started",
            file=sys.stderr,
            flush=True,
        )
        return

    if not os.path.isfile(CONFIG):
        print(
            f"[cloudflared-tunnel] config not found at {CONFIG}, tunnel not started",
            file=sys.stderr,
            flush=True,
        )
        return

    existing = _read_pid()
    if existing is not None and _pid_alive(existing):
        print(
            f"[cloudflared-tunnel] tunnel '{TUNNEL}' already running "
            f"(pid={existing}), skip start",
            flush=True,
        )
        return
    if existing is not None:
        _clear_pid()

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(LOG_FILE, "ab") as logf:
            proc = subprocess.Popen(
                [cloudflared, "tunnel", "--config", CONFIG, "run", TUNNEL],
                stdout=logf,
                stderr=subprocess.STDOUT,
                **_popen_kwargs(),
            )
    except Exception as e:
        print(
            f"[cloudflared-tunnel] FAILED to start '{TUNNEL}': {e}",
            file=sys.stderr,
            flush=True,
        )
        return

    time.sleep(max(0.0, STARTUP_GRACE_SEC))
    code = proc.poll()
    if code is not None:
        tail = _log_tail()
        extra = f": {tail}" if tail else ""
        print(
            f"[cloudflared-tunnel] FAILED — process exited {code} within "
            f"{STARTUP_GRACE_SEC}s (tunnel='{TUNNEL}'){extra}",
            file=sys.stderr,
            flush=True,
        )
        _clear_pid()
        return

    _write_pid(proc.pid)
    print(
        f"[cloudflared-tunnel] Started tunnel '{TUNNEL}' "
        f"(pid={proc.pid}, log={LOG_FILE}) on gateway:startup",
        flush=True,
    )

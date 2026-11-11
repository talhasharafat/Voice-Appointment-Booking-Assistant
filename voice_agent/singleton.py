"""
Ensure only one clinic-booking agent worker runs on this machine.

Starting a second `python -m voice_agent.pipeline start` used to fail with
OSError 48 on 0.0.0.0:8081. This module stops the previous worker (and frees
the health port) before the new process binds.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("voice_agent.singleton")

PID_FILE = Path(__file__).resolve().parent / ".agent.pid"


def health_port() -> int:
    """LiveKit worker HTTP health port. 0 = OS-assigned (no fixed bind conflict)."""
    raw = (os.getenv("VOICE_AGENT_PORT") or "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _pids_matching_agent() -> list[int]:
    """Return PIDs of other voice_agent.pipeline workers (not this process)."""
    me = os.getpid()
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "voice_agent.pipeline"],
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid != me and pid != os.getppid():
            pids.append(pid)
    return pids


def _pids_listening_on(port: int) -> list[int]:
    if port <= 0:
        return []
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", "-iTCP:{}".format(port), "-sTCP:LISTEN", "-t"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    me = os.getpid()
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid != me:
            pids.append(pid)
    return pids


def _stop_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        logger.warning("no permission to stop pid=%s", pid)
        return
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def claim_singleton() -> None:
    """
    Stop any previous agent worker / health-port holder, then write our pid file.

    Safe to call at the start of main() before AgentServer binds.
    """
    port = health_port()
    targets = set(_pids_matching_agent())
    if port:
        targets.update(_pids_listening_on(port))

    # Also honor a stale pid file from a previous run.
    if PID_FILE.exists():
        try:
            old = int(PID_FILE.read_text().strip() or "0")
            if old and old != os.getpid():
                targets.add(old)
        except ValueError:
            pass

    if targets:
        logger.warning(
            "stopping previous voice agent process(es) so only one worker runs: %s",
            sorted(targets),
        )
        for pid in sorted(targets):
            _stop_pid(pid)
        # Brief pause so TIME_WAIT / LISTEN sockets release.
        time.sleep(0.5)

    PID_FILE.write_text(str(os.getpid()))
    logger.info(
        "singleton claimed pid=%s health_port=%s (0 means ephemeral)",
        os.getpid(),
        port,
    )

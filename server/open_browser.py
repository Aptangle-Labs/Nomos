"""Best-effort browser launch for start.sh; never part of server startup."""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request


def browser_command() -> str | None:
    if os.environ.get("NOMOS_NO_BROWSER") == "1":
        return None
    # Remote shells should not open a browser on the server's desktop.
    if (
        os.environ.get("SSH_CONNECTION")
        or os.environ.get("SSH_TTY")
        or os.environ.get("SSH_CLIENT")
    ):
        return None
    system = platform.system()
    if system == "Darwin":
        return shutil.which("open")
    if system == "Linux" and (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return shutil.which("xdg-open")
    return None


def browser_url(host: str, port: int) -> str:
    host = host.strip("[]")
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}/"


def parent_alive(parent_pid: int) -> bool:
    """The helper is orphaned to init on purpose (so uvicorn never has a
    zombie child to reap); the server it belongs to is checked by signal 0,
    which works regardless of parentage."""
    if parent_pid <= 0:
        return False
    try:
        os.kill(parent_pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def open_when_ready(host: str, port: int, parent_pid: int, timeout: float = 30) -> None:
    command = browser_command()
    if command is None:
        return
    url = browser_url(host, port)
    # Local readiness must not depend on an HTTP proxy configured in the shell.
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not parent_alive(parent_pid):
            return  # launcher/server exited, so there is nothing to open
        try:
            with client.open(url, timeout=min(1, max(0.01, deadline - time.monotonic()))) as response:
                ready = response.status == 200
        except (OSError, urllib.error.URLError):
            ready = False
        if ready:
            if not parent_alive(parent_pid):
                return
            try:
                subprocess.run(
                    [command, url], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass  # missing desktop, failed opener, or shutdown must be harmless
            return
        time.sleep(0.2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--parent-pid", required=True, type=int)
    args = parser.parse_args()
    open_when_ready(args.host, args.port, args.parent_pid)


if __name__ == "__main__":
    main()

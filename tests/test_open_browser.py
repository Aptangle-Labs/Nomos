"""Exercise readiness and launch isolation without opening a real browser."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import pytest

from server import open_browser


@pytest.fixture(autouse=True)
def desktop(monkeypatch):
    for key in ("NOMOS_NO_BROWSER", "SSH_CONNECTION", "SSH_TTY", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(open_browser.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(open_browser.shutil, "which", lambda command: f"/bin/{command}")


@pytest.mark.parametrize("host,expected", [
    ("0.0.0.0", "127.0.0.1"), ("::", "[::1]"), ("[::1]", "[::1]"),
    ("localhost", "localhost"), ("192.0.2.1", "192.0.2.1"),
])
def test_browser_url(host, expected):
    assert open_browser.browser_url(host, 8486) == f"http://{expected}:8486/"


def test_platform_and_headless_selection(monkeypatch):
    assert open_browser.browser_command() == "/bin/open"
    monkeypatch.setattr(open_browser.platform, "system", lambda: "Linux")
    assert open_browser.browser_command() is None
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert open_browser.browser_command() == "/bin/xdg-open"
    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.setenv("DISPLAY", ":0")
    assert open_browser.browser_command() == "/bin/xdg-open"
    monkeypatch.setenv("SSH_CONNECTION", "remote")
    assert open_browser.browser_command() is None
    monkeypatch.delenv("SSH_CONNECTION")
    monkeypatch.setattr(open_browser.shutil, "which", lambda command: None)
    assert open_browser.browser_command() is None


def test_opt_out_does_not_even_probe(monkeypatch):
    monkeypatch.setenv("NOMOS_NO_BROWSER", "1")
    build = Mock(side_effect=AssertionError("must not probe"))
    monkeypatch.setattr(open_browser.urllib.request, "build_opener", build)
    open_browser.open_when_ready("127.0.0.1", 8486, os.getppid())
    build.assert_not_called()


@pytest.mark.parametrize("failure", [None, OSError("no desktop"), subprocess.TimeoutExpired("open", 5)])
def test_waits_for_real_http_readiness_then_opens_once(monkeypatch, failure):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(503 if len(requests) == 1 else 200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    launch = Mock(side_effect=failure)
    monkeypatch.setattr(open_browser.subprocess, "run", launch)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    try:
        port = server.server_port
        open_browser.open_when_ready("127.0.0.1", port, os.getppid(), timeout=2)
        assert requests == ["/", "/"]
        launch.assert_called_once()
        assert launch.call_args.args[0] == ["/bin/open", f"http://127.0.0.1:{port}/"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_timeout_and_dead_parent_never_launch(monkeypatch):
    launch = Mock()
    monkeypatch.setattr(open_browser.subprocess, "run", launch)
    client = Mock()
    client.open.side_effect = OSError("not ready")
    monkeypatch.setattr(open_browser.urllib.request, "build_opener", lambda *args: client)
    open_browser.open_when_ready("127.0.0.1", 8486, os.getppid(), timeout=0.05)
    assert client.open.call_count == 1
    client.reset_mock()
    open_browser.open_when_ready("127.0.0.1", 8486, -1, timeout=1)
    client.open.assert_not_called()
    launch.assert_not_called()


def test_start_script_opt_out_and_foreground_exec(tmp_path):
    """Run the shell launcher with setup/server stand-ins, preserving its PID."""
    source = Path(__file__).resolve().parents[1] / "start.sh"
    (tmp_path / "start.sh").write_text(source.read_text())
    bindir = tmp_path / ".venv" / "bin"
    bindir.mkdir(parents=True)
    python = bindir / "python"
    python.write_text('#!/bin/sh\nprintf "%s %s\\n" "$$" "$*" >> "$CALL_LOG"\n')
    python.chmod(0o755)
    uv = bindir / "uv"
    uv.write_text("#!/bin/sh\nexit 0\n")
    uv.chmod(0o755)
    log = tmp_path / "calls"
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", CALL_LOG=str(log),
               NOMOS_NO_BROWSER="1", NOMOS_HOST="127.0.0.1", NOMOS_PORT="8486")
    proc = subprocess.Popen(["bash", "start.sh"], cwd=tmp_path, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = proc.communicate(timeout=5)
    assert proc.returncode == 0, (stdout, stderr)
    calls = log.read_text().splitlines()
    assert len(calls) == 2
    assert "-m server.migrate" in calls[0]
    assert calls[1] == f"{proc.pid} -m uvicorn server.main:app --host 127.0.0.1 --port 8486 --workers 1 --no-access-log"


def test_parent_alive_by_signal_not_parentage():
    """The helper is orphaned on purpose, so aliveness must come from
    signal 0 against the server pid, not getppid."""
    assert open_browser.parent_alive(os.getpid())
    dead = subprocess.Popen(["true"])  # noqa: S603,S607 - trivial child
    dead.wait()
    assert not open_browser.parent_alive(dead.pid) or dead.poll() is None
    assert not open_browser.parent_alive(-1)
    assert not open_browser.parent_alive(0)


def test_start_script_enabled_path_launches_orphaned_helper(tmp_path):
    """With the browser enabled, the launcher invokes the helper module in a
    subshell (so exec'd uvicorn never owns it) alongside migrate + uvicorn."""
    source = Path(__file__).resolve().parents[1] / "start.sh"
    (tmp_path / "start.sh").write_text(source.read_text())
    bindir = tmp_path / ".venv" / "bin"
    bindir.mkdir(parents=True)
    python = bindir / "python"
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n')
    python.chmod(0o755)
    uv = bindir / "uv"
    uv.write_text("#!/bin/sh\nexit 0\n")
    uv.chmod(0o755)
    log = tmp_path / "calls"
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", CALL_LOG=str(log),
               NOMOS_HOST="127.0.0.1", NOMOS_PORT="8486")
    env.pop("NOMOS_NO_BROWSER", None)
    proc = subprocess.Popen(["bash", "start.sh"], cwd=tmp_path, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = proc.communicate(timeout=5)
    assert proc.returncode == 0, (stdout, stderr)
    deadline = time.monotonic() + 2
    calls = []
    while time.monotonic() < deadline and len(calls) < 3:
        calls = log.read_text().splitlines() if log.exists() else []
        time.sleep(0.05)
    assert any("-m server.open_browser" in line and "--parent-pid" in line for line in calls), calls
    assert any("-m uvicorn server.main:app" in line for line in calls)

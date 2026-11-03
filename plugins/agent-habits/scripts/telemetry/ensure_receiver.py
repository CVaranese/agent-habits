"""Start receiver.py in the background unless it already answers on the OTLP endpoint's port.

Run by the plugin's SessionStart hook, so every Claude Code session (any project) has a receiver to export to. Does
nothing unless telemetry is on and exported to this machine: CLAUDE_CODE_ENABLE_TELEMETRY=1 and
OTEL_EXPORTER_OTLP_ENDPOINT on 127.0.0.1 / localhost (user settings "env"; README "Telemetry"). Returns at once; the
receiver outlives the session. Prints nothing (hook output reaches the model).
"""
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def local_port(env=os.environ):
    """The endpoint's port when telemetry goes to this machine, else None."""
    if env.get("CLAUDE_CODE_ENABLE_TELEMETRY") != "1":
        return None
    url = urllib.parse.urlparse(env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "")
    if url.hostname not in ("127.0.0.1", "localhost"):
        return None
    return url.port or 4318


def running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as r:
            return r.read() == b"claude-telemetry-receiver"
    except OSError:
        return False


def main():
    port = local_port()
    if port is None or running(port):
        return
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(
        [str(pythonw if pythonw.exists() else exe), "-I", str(Path(__file__).with_name("receiver.py")),
         "--port", str(port)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags, close_fds=True, start_new_session=sys.platform != "win32")


if __name__ == "__main__":
    try:
        main()
    except Exception:   # a hook must never break session start
        pass

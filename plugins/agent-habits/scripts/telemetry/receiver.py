"""Local OTLP/HTTP (JSON) receiver for Claude Code telemetry.

Claude Code exports metrics and log events when telemetry is enabled (user settings "env":
CLAUDE_CODE_ENABLE_TELEMETRY=1, OTEL_*_EXPORTER=otlp, OTEL_EXPORTER_OTLP_PROTOCOL=http/json,
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318). This server appends every POSTed body as one JSON line to
<out>/<YYYY-MM-DD>/<signal>.jsonl (signal = metrics / logs / traces); analysis.py reads them. <out> defaults to
$AGENT_TELEMETRY_DIR, else ~/.claude/agent-telemetry.

Started on demand by ensure_receiver.py (the plugin's SessionStart hook); stdlib only, binds 127.0.0.1.

    python -I receiver.py [--port 4318] [--out <dir>]
"""
import argparse
import datetime
import gzip
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def data_dir():
    return Path(os.environ.get("AGENT_TELEMETRY_DIR") or Path.home() / ".claude" / "agent-telemetry")


SIGNALS = {"/v1/metrics": "metrics", "/v1/logs": "logs", "/v1/traces": "traces"}
_lock = threading.Lock()


def make_handler(out_dir):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            signal = SIGNALS.get(self.path.split("?")[0])
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if signal is None:
                self.send_response(404)
                self.end_headers()
                return
            status = 200
            try:
                if self.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                if "json" not in (self.headers.get("Content-Type") or ""):
                    # Protobuf isn't decoded here; keep a marker so a wrong protocol setting shows up in the data.
                    record = {"_unsupported_content_type": self.headers.get("Content-Type"), "_bytes": len(body)}
                else:
                    record = json.loads(body)
                    if not isinstance(record, dict):
                        raise ValueError(f"top-level JSON {type(record).__name__}, not an object")
            except (OSError, EOFError, ValueError) as e:
                # Bad body: keep a marker (stderr goes nowhere) and answer 400 so the exporter doesn't retry it.
                record = {"_bad_body": str(e)[:200], "_bytes": len(body)}
                status = 400
            record["_received"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
            day = datetime.date.today().isoformat()
            path = out_dir / day / f"{signal}.jsonl"
            with _lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps(record, separators=(",", ":")) + "\n")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_GET(self):
            # Health check for ensure_receiver.py.
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()
            if self.path == "/health":
                self.wfile.write(b"claude-telemetry-receiver")

        def log_message(self, *args):
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4318)
    ap.add_argument("--out", type=Path, default=data_dir())
    a = ap.parse_args()
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.out)).serve_forever()


if __name__ == "__main__":
    main()

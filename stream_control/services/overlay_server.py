from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from stream_control.core.models import OverlaySettings

OVERLAY_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Now Playing</title>
  <style>
    :root {
      --bg: rgba(8, 13, 19, 0.82);
      --border: rgba(91, 236, 203, 0.35);
      --accent: #5beccb;
      --text: #eef5fb;
      --muted: #adc0d0;
    }
    body {
      margin: 0;
      background: transparent;
      font-family: "Segoe UI", "SF Pro Display", "Noto Sans", sans-serif;
      color: var(--text);
    }
    .wrap {
      display: flex;
      align-items: center;
      gap: 18px;
      min-width: 360px;
      max-width: 720px;
      margin: 20px;
      padding: 16px 18px;
      border: 1px solid var(--border);
      border-radius: 18px;
      background: linear-gradient(135deg, var(--bg), rgba(15, 29, 40, 0.92));
      backdrop-filter: blur(16px);
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.28);
    }
    .pulse {
      width: 14px;
      height: 14px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 0 0 rgba(91, 236, 203, 0.65);
      animation: pulse 1.8s infinite;
      flex: 0 0 auto;
    }
    .text {
      display: grid;
      gap: 4px;
      flex: 1 1 auto;
    }
    .eyebrow {
      text-transform: uppercase;
      font-size: 11px;
      letter-spacing: 0.18em;
      color: var(--muted);
    }
    .title {
      font-size: 28px;
      font-weight: 700;
      line-height: 1.05;
    }
    .artist {
      font-size: 14px;
      color: var(--muted);
    }
    .artist:empty {
      display: none;
    }
    .meta {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }
    .time {
      font-size: 13px;
      color: var(--muted);
      white-space: nowrap;
    }
    .progress {
      width: 100%;
      height: 6px;
      border-radius: 999px;
      overflow: hidden;
      background: rgba(173, 192, 208, 0.18);
    }
    .progress-fill {
      height: 100%;
      width: 0%;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--accent), #9bf6e2);
      transition: width 0.2s linear;
    }
    .stopped .pulse {
      background: #7e92a5;
      animation: none;
    }
    @keyframes pulse {
      0% { box-shadow: 0 0 0 0 rgba(91, 236, 203, 0.65); }
      70% { box-shadow: 0 0 0 16px rgba(91, 236, 203, 0); }
      100% { box-shadow: 0 0 0 0 rgba(91, 236, 203, 0); }
    }
  </style>
</head>
<body>
  <div class="wrap stopped" id="card">
    <div class="pulse"></div>
    <div class="text">
      <div class="eyebrow" id="status">Standby</div>
      <div class="title" id="title">No Music Playing</div>
      <div class="meta">
        <div class="artist" id="artist"></div>
        <div class="time" id="time">0:00 / 0:00</div>
      </div>
      <div class="progress">
        <div class="progress-fill" id="progress-fill"></div>
      </div>
    </div>
  </div>
  <script>
    const card = document.getElementById("card");
    const statusNode = document.getElementById("status");
    const titleNode = document.getElementById("title");
    const artistNode = document.getElementById("artist");
    const timeNode = document.getElementById("time");
    const progressFillNode = document.getElementById("progress-fill");

    async function refresh() {
      try {
        const response = await fetch("/api/now-playing", { cache: "no-store" });
        const payload = await response.json();
        statusNode.textContent = payload.status || "Standby";
        titleNode.textContent = payload.title || "No Music Playing";
        artistNode.textContent = payload.artist || "";
        timeNode.textContent = `${payload.elapsed_label || "0:00"} / ${payload.duration_label || "0:00"}`;
        progressFillNode.style.width = `${payload.progress_percent || 0}%`;
        card.classList.toggle("stopped", !payload.is_playing);
      } catch (error) {
        statusNode.textContent = "Offline";
      }
    }

    refresh();
    setInterval(refresh, 1000);
  </script>
</body>
</html>
"""


@dataclass(frozen=True, slots=True)
class OverlayServerStatus:
    enabled: bool
    running: bool
    url: str
    last_error: str = ""


class OverlayServer:
    def __init__(
        self,
        settings: OverlaySettings,
        state_provider: Callable[[], dict[str, object]],
    ) -> None:
        self._settings = settings
        self._state_provider = state_provider
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.last_error = ""
        self._running = False

    @property
    def url(self) -> str:
        return self._settings.now_playing_url

    @property
    def is_running(self) -> bool:
        return self._running and self._server is not None

    def status(self) -> OverlayServerStatus:
        return OverlayServerStatus(
            enabled=self._settings.enabled,
            running=self.is_running,
            url=self.url,
            last_error=self.last_error,
        )

    def start(self) -> bool:
        if not self._settings.enabled or self._server is not None:
            self._running = self._server is not None
            return self.is_running

        state_provider = self._state_provider

        class RequestHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/api/now-playing":
                    payload = json.dumps(state_provider()).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return

                if parsed.path == "/overlay/now-playing":
                    payload = OVERLAY_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return

                self.send_response(404)
                self.end_headers()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        try:
            self._server = ThreadingHTTPServer((self._settings.host, self._settings.port), RequestHandler)
        except OSError as exc:
            self.last_error = str(exc)
            self._server = None
            self._thread = None
            self._running = False
            return False

        self.last_error = ""
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._running = True
        return True

    def stop(self) -> None:
        if self._server is None:
            self._running = False
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None
        self._running = False

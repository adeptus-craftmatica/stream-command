from __future__ import annotations

import json
import threading
from collections.abc import Callable
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
      <div class="artist" id="artist"></div>
    </div>
  </div>
  <script>
    const card = document.getElementById("card");
    const statusNode = document.getElementById("status");
    const titleNode = document.getElementById("title");
    const artistNode = document.getElementById("artist");

    async function refresh() {
      try {
        const response = await fetch("/api/now-playing", { cache: "no-store" });
        const payload = await response.json();
        statusNode.textContent = payload.status || "Standby";
        titleNode.textContent = payload.title || "No Music Playing";
        artistNode.textContent = payload.artist || "";
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

    @property
    def url(self) -> str:
        return self._settings.now_playing_url

    def start(self) -> None:
        if not self._settings.enabled or self._server is not None:
            return

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
            return

        self.last_error = ""
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

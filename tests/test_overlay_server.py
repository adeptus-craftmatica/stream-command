import socket

from stream_control.core.models import OverlaySettings
from stream_control.services.overlay_server import OverlayServer


def test_overlay_server_reports_bind_error_when_port_is_in_use() -> None:
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    host, port = blocker.getsockname()

    server = OverlayServer(
        OverlaySettings(host=host, port=port, enabled=True),
        lambda: {"title": "Demo", "artist": "Tester", "status": "Playing", "is_playing": True},
    )
    started = server.start()
    status = server.status()

    blocker.close()

    assert started is False
    assert status.running is False
    assert status.url.endswith(f":{port}/overlay/now-playing")
    assert status.last_error

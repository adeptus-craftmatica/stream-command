import asyncio

from stream_control.services.twitch_service import TwitchCredentials, TwitchService


class _FakeTwitchService(TwitchService):
    def __init__(self) -> None:
        super().__init__()
        self.state = {
            "broadcaster_id": "141981764",
            "broadcaster_name": "TwitchDev",
            "title": "Old Title",
            "category_id": "509658",
            "category_name": "Just Chatting",
        }
        self.last_patch: dict[str, str] = {}

    def _request_json(self, method, path, client_id, access_token, *, query=None, body=None):  # type: ignore[override]
        assert client_id == "client-id"
        assert access_token == "token-value"
        if path == "/users":
            return {"data": [{"id": self.state["broadcaster_id"]}]}
        if path == "/search/categories":
            return {"data": [{"id": "509660", "name": "Art"}]}
        if path == "/channels" and method == "PATCH":
            self.last_patch = dict(body or {})
            if "title" in self.last_patch:
                self.state["title"] = self.last_patch["title"]
            if "game_id" in self.last_patch:
                self.state["category_id"] = self.last_patch["game_id"]
                self.state["category_name"] = "Art"
            return {}
        if path == "/channels" and method == "GET":
            return {
                "data": [
                    {
                        "broadcaster_id": self.state["broadcaster_id"],
                        "broadcaster_name": self.state["broadcaster_name"],
                        "title": self.state["title"],
                        "game_id": self.state["category_id"],
                        "game_name": self.state["category_name"],
                    }
                ]
            }
        raise AssertionError(f"Unexpected Twitch request: {method} {path}")


def test_twitch_service_resolves_broadcaster_and_updates_channel_info() -> None:
    service = _FakeTwitchService()
    credentials = TwitchCredentials(client_id="client-id", access_token="Bearer token-value")

    info = asyncio.run(service.get_channel_info(credentials))
    categories = asyncio.run(service.search_categories(credentials, "art"))
    updated = asyncio.run(
        service.update_channel_info(
            credentials,
            title="Fresh Title",
            category_id="509660",
        )
    )

    assert info.broadcaster_id == "141981764"
    assert categories[0].name == "Art"
    assert service.last_patch == {"title": "Fresh Title", "game_id": "509660"}
    assert updated.title == "Fresh Title"
    assert updated.category_name == "Art"

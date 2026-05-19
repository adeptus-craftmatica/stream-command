import asyncio

from PySide6.QtWidgets import QApplication

from stream_control.services.twitch_chat_service import (
    AutoModQueueItem,
    ChatActivity,
    ChatMessage,
    TwitchChatService,
)


def test_twitch_chat_service_parses_privmsg_and_roomstate() -> None:
    parsed = TwitchChatService._parse_irc_line(
        "@badge-info=;badges=moderator/1;color=#1E90FF;display-name=PixelPilot;first-msg=1;id=abc123;room-id=42;tmi-sent-ts=1716111111111 "
        ":pixelpilot!pixelpilot@pixelpilot.tmi.twitch.tv PRIVMSG #streamcontrol :Hello, chat!"
    )

    assert parsed["command"] == "PRIVMSG"
    assert parsed["tags"]["display-name"] == "PixelPilot"
    assert parsed["trailing"] == "Hello, chat!"


def test_twitch_chat_service_simulator_supports_chat_moderation_and_engagement() -> None:
    app = QApplication.instance() or QApplication([])
    service = TwitchChatService()
    messages: list[ChatMessage] = []
    activities: list[ChatActivity] = []
    statuses: list[tuple[bool, str]] = []
    room_states: list[dict[str, object]] = []
    viewer_snapshots: list[list] = []
    automod_snapshots: list[list[AutoModQueueItem]] = []
    summaries: list[dict[str, object]] = []

    service.message_received.connect(messages.append)
    service.activity_received.connect(activities.append)
    service.connection_changed.connect(lambda connected, message: statuses.append((connected, message)))
    service.room_state_changed.connect(room_states.append)
    service.viewer_cards_changed.connect(lambda cards: viewer_snapshots.append(cards))
    service.automod_queue_changed.connect(lambda items: automod_snapshots.append(items))
    service.subscription_summary_changed.connect(summaries.append)

    async def scenario() -> None:
        await service.connect_simulated("demochannel")
        await service.send_message("Testing simulator send")
        await service.send_announcement("Show starts soon", "green")
        await service.create_poll("What next?", ["Keep gaming", "Swap scenes"], 60)
        await service.create_prediction("Do we clutch this?", ["Yes", "No"], 90)
        service._handle_automod_hold_event(
            {
                "message_id": "held-1",
                "user_id": "viewer-1",
                "user_login": "viewerone",
                "user_name": "ViewerOne",
                "message": {"text": "Needs a review"},
            }
        )
        await service.approve_automod_message("held-1")
        await service.disconnect(silent=True)

    asyncio.run(scenario())

    assert any("simulator" in message.lower() for _, message in statuses)
    assert room_states[-1]["channel"] == "demochannel"
    assert any(message.display_name == "You" for message in messages)
    assert {activity.kind for activity in activities} >= {"announcement", "poll", "prediction"}
    assert viewer_snapshots and len(viewer_snapshots[-1]) >= 1
    assert summaries and summaries[-1]["mode"] == "disconnected"
    assert automod_snapshots and automod_snapshots[-1] == []
    assert app is not None

"""B3: opening the main menu still wipes a busy flow, with fewer blind deletes.

The window of recent message ids is a safety net for the messages the bot cannot
track (a restart forgets the stored ids), so it has to cover one busy flow: an
album of ten files, its status bubble, the result screen and the notices around
them. Everything else is deleted from the tracked list.
"""

from __future__ import annotations

from src.bot import views

# The widest flow the bot can leave behind in one go: ten files of an album, the
# status bubble of the batch, the result screen and a few notices.
BUSY_FLOW = 15


class _Bot:
    """Records the batched and the single deletes the wipe asks for."""

    def __init__(self) -> None:
        self.batches: list[list[int]] = []
        self.single: list[int] = []

    async def delete_messages(self, chat_id: int, message_ids: list[int]) -> bool:
        self.batches.append(list(message_ids))
        return True

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        self.single.append(message_id)
        return True


class ChatState:
    """The part of the FSM context the wipe reads and writes."""

    def __init__(self, data: dict[str, object] | None = None) -> None:
        self.data = dict(data or {})

    async def get_data(self) -> dict[str, object]:
        return dict(self.data)

    async def update_data(self, **values: object) -> None:
        self.data.update(values)


def test_the_window_still_covers_a_busy_flow() -> None:
    assert views._WIPE_WINDOW >= BUSY_FLOW
    # The window alone never needs more than one delete request.
    assert views._WIPE_WINDOW <= views._MAX_IDS_PER_CALL
    # It is a safety net, not a sweep of the whole chat.
    assert views._WIPE_WINDOW < views._MAX_TRACKED_MESSAGES


async def test_opening_the_main_menu_wipes_the_tracked_and_the_recent_messages() -> None:
    bot = _Bot()
    state = ChatState({"cleanup_ids": [91, 92], "screen_message_id": 93})

    await views.clear_tracked(bot, 55, state, upto=100)

    deleted = set(bot.batches[0]) | set(bot.single)
    # The screens the bot knows about, the menu message and the recent window.
    assert {91, 92, 93, 100} <= deleted
    assert set(range(100 - views._WIPE_WINDOW + 1, 101)) <= deleted
    # Nothing older than the window is touched, so an older chat survives.
    assert 100 - views._WIPE_WINDOW not in deleted
    # One request for the whole wipe.
    assert len(bot.batches) == 1
    assert len(bot.batches[0]) <= views._MAX_IDS_PER_CALL
    # The log is empty afterwards, so the next menu does not repeat it.
    assert state.data["cleanup_ids"] == []
    assert state.data["screen_message_id"] is None

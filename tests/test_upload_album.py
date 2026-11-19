"""B1: an album removes the messages of the files it stored, and only those.

A file that could not be stored keeps its message in the chat, so the user can
still read what happened and send the same file again.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.bot.routers import upload


class _Bot:
    """Records every delete the album screen asks for."""

    def __init__(self) -> None:
        self.deleted: list[int] = []

    async def delete_messages(self, chat_id: int, message_ids: list[int]) -> bool:
        self.deleted.extend(message_ids)
        return True

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        self.deleted.append(message_id)
        return True


class _Files:
    """The real service interface, failing exactly one file by name."""

    def __init__(self, fail_name: str) -> None:
        self.fail_name = fail_name
        self.saved: list[str] = []

    async def save_from_message(self, message, candidate):
        if candidate.file_name == self.fail_name:
            raise RuntimeError("storage group is not writable")
        self.saved.append(candidate.file_name)
        return SimpleNamespace(id=str(len(self.saved)))


class _Message:
    """One document of the album, with the one answer call the flow makes."""

    def __init__(self, message_id: int, name: str) -> None:
        self.chat = SimpleNamespace(id=5)
        self.message_id = message_id
        self.from_user = SimpleNamespace(id=7)
        self.document = SimpleNamespace(
            file_name=name,
            mime_type="application/pdf",
            file_id=f"file-{message_id}",
            file_unique_id=f"unique-{message_id}",
            file_size=10,
        )
        self.photo = self.video = self.audio = None
        self.voice = self.animation = self.video_note = None
        self.status_id = 5000 + message_id

    async def answer(self, text: str):
        return SimpleNamespace(
            chat=SimpleNamespace(id=self.chat.id),
            message_id=self.status_id,
            text=text,
        )


async def test_a_failed_file_of_an_album_keeps_its_message(monkeypatch) -> None:
    bot = _Bot()
    files = _Files(fail_name="broken.pdf")
    services = SimpleNamespace(
        files=files,
        preferences=SimpleNamespace(show_saved_screen=lambda _user_id: True),
    )

    async def reset_flow(state, **kwargs) -> None:
        return None

    async def remember(state, *message_ids: int) -> None:
        return None

    captured: dict[str, object] = {}

    async def navigate(message, state, bot, services, target, **kwargs) -> None:
        captured["screen"] = target

    monkeypatch.setattr(upload.views, "reset_flow", reset_flow)
    monkeypatch.setattr(upload.views, "remember", remember)
    monkeypatch.setattr(upload.navigation, "navigate_message", navigate)

    stored = _Message(11, "report.pdf")
    failed = _Message(12, "broken.pdf")

    await upload._handle_album([stored, failed], services, SimpleNamespace(), bot)

    # The stored file and the status bubble are gone; the failed file stays.
    assert bot.deleted == [stored.message_id, stored.status_id]
    assert failed.message_id not in bot.deleted
    assert failed.status_id not in bot.deleted
    assert files.saved == ["report.pdf"]
    assert captured["screen"]["k"] == "saved"
    assert captured["screen"]["x"] == 1

"""Per-user preferences: the interface language and how the screens behave.

Telegram hands the bot no place to keep a setting, and the FSM storage is in
memory (so a restart would forget it). The language of each user, and the three
display choices of «⚙️ Preferences», therefore live in a tiny JSON file next to
the emergency registry, written atomically.

English is the default and the saved-file card is shown by default: a user who
never opened «Settings» sees English and the usual result screen, and a missing or
unreadable file simply means "everybody uses the defaults".
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from src.core.i18n import DEFAULT_LANGUAGE, Language, parse_language

logger = logging.getLogger(__name__)


def _read_flags(raw: dict[str, object], key: str, target: dict[int, bool]) -> None:
    """Read one of the on/off maps; only a real JSON boolean is accepted."""
    rows = raw.get(key)
    if not isinstance(rows, dict):
        return
    for user_key, value in rows.items():
        if isinstance(value, bool):
            try:
                target[int(user_key)] = value
                continue
            except (TypeError, ValueError):
                pass
        logger.warning("Skipped an invalid %s entry: %r", key, user_key)


class UserPreferences:
    """The persisted language and screen choices of each Telegram user."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._languages: dict[int, Language] = {}
        # False only when the user turned the «The file was saved» card off.
        self._saved_screen: dict[int, bool] = {}
        # True only when opened files must survive «↩️ Back» until the main menu.
        self._keep_sent: dict[int, bool] = {}
        # True only when a new file replaces the previous one in the chat.
        self._single_view: dict[int, bool] = {}

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        """Read the file once; a missing or broken one means the defaults."""
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning(
                "The preferences file at %s is unreadable; every user starts in English",
                self._path,
                exc_info=True,
            )
            return
        if not isinstance(raw, dict):
            return
        rows = raw.get("languages")
        if isinstance(rows, dict):
            for key, value in rows.items():
                try:
                    self._languages[int(key)] = parse_language(value)
                except (TypeError, ValueError):
                    logger.warning("Skipped an invalid language entry: %r", key)

        _read_flags(raw, "saved_screen", self._saved_screen)
        _read_flags(raw, "keep_sent", self._keep_sent)
        _read_flags(raw, "single_view", self._single_view)

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "languages": {
                    str(user_id): lang.value
                    for user_id, lang in self._languages.items()
                },
                "saved_screen": {
                    str(user_id): show
                    for user_id, show in self._saved_screen.items()
                },
                "keep_sent": {
                    str(user_id): keep
                    for user_id, keep in self._keep_sent.items()
                },
                "single_view": {
                    str(user_id): single
                    for user_id, single in self._single_view.items()
                },
            },
            indent=2,
        )
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self._path)

    def language_for(self, user_id: int | None) -> Language:
        """The language of one user (English when nothing was chosen)."""
        if user_id is None:
            return DEFAULT_LANGUAGE
        return self._languages.get(int(user_id), DEFAULT_LANGUAGE)

    async def set_language(self, user_id: int, language: Language | str) -> Language:
        parsed = parse_language(language)
        self._languages[int(user_id)] = parsed
        self._write()
        return parsed

    def show_saved_screen(self, user_id: int | None) -> bool:
        """Whether an upload ends with its «The file was saved» card (default on)."""
        if user_id is None:
            return True
        return self._saved_screen.get(int(user_id), True)

    async def set_saved_screen(self, user_id: int, show: bool) -> bool:
        """Remember the user's choice about the saved-file card."""
        self._saved_screen[int(user_id)] = bool(show)
        self._write()
        return bool(show)

    def keep_sent_files(self, user_id: int | None) -> bool:
        """Whether opened files survive «↩️ Back» (default off: they are removed)."""
        if user_id is None:
            return False
        return self._keep_sent.get(int(user_id), False)

    async def set_keep_sent_files(self, user_id: int, keep: bool) -> bool:
        """Remember the user's choice about keeping the files opened from a screen."""
        self._keep_sent[int(user_id)] = bool(keep)
        self._write()
        return bool(keep)

    def single_file_view(self, user_id: int | None) -> bool:
        """Whether a new file replaces the previous one (default off)."""
        if user_id is None:
            return False
        return self._single_view.get(int(user_id), False)

    async def set_single_file_view(self, user_id: int, enabled: bool) -> bool:
        """Remember the user's choice about showing one file at a time."""
        self._single_view[int(user_id)] = bool(enabled)
        self._write()
        return bool(enabled)

    @property
    def total(self) -> int:
        return len(self._languages)


__all__ = ["UserPreferences"]

"""Per-user preferences: the interface language and the saved-file screen.

Telegram hands the bot no place to keep a setting, and the FSM storage is in
memory (so a restart would forget it). The language of each user, and whether an
upload ends with the «✅ The file was saved» card, therefore live in a tiny JSON
file next to the emergency registry, written atomically.

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


class UserPreferences:
    """The persisted language and screen choices of each Telegram user."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._languages: dict[int, Language] = {}
        # False only when the user turned the «The file was saved» card off.
        self._saved_screen: dict[int, bool] = {}

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
        rows = raw.get("languages") if isinstance(raw, dict) else None
        if isinstance(rows, dict):
            for key, value in rows.items():
                try:
                    self._languages[int(key)] = parse_language(value)
                except (TypeError, ValueError):
                    logger.warning("Skipped an invalid language entry: %r", key)

        screens = raw.get("saved_screen") if isinstance(raw, dict) else None
        if isinstance(screens, dict):
            for key, value in screens.items():
                # Only a real JSON boolean counts; strings are never truthy here.
                if not isinstance(value, bool):
                    logger.warning("Skipped an invalid saved-screen entry: %r", key)
                    continue
                try:
                    self._saved_screen[int(key)] = value
                except (TypeError, ValueError):
                    logger.warning("Skipped an invalid saved-screen entry: %r", key)

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

    @property
    def total(self) -> int:
        return len(self._languages)


__all__ = ["UserPreferences"]

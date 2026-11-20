"""The «🛡 Group permissions» screen and the two keyboards that reach it.

The screen has two jobs: a single summary line that says whether the rights are
all enabled, partly enabled or all off, and a per-group list naming every missing
right together with what it breaks. The keyboards are checked too, because the
screen is only reachable through the button «General status» builds.
"""

from __future__ import annotations

from types import SimpleNamespace

from conftest import make_settings
from src.bot import keyboards
from src.bot.keyboards import MenuCB
from src.bot.status import permissions_text, status_text
from src.bot.views import stats_text
from src.services.storage import StorageChatCheck
from src.utils.filetypes import FileCategory

DOCUMENTS = FileCategory.document
IMAGES = FileCategory.image


def make_check(
    chat_id: int,
    *,
    categories: tuple[FileCategory, ...] = (DOCUMENTS,),
    reachable: bool = True,
    is_admin: bool = True,
    missing: tuple[str, ...] = (),
    is_fallback: bool = False,
    reason: str | None = None,
) -> StorageChatCheck:
    return StorageChatCheck(
        chat_id=chat_id,
        categories=frozenset(categories),
        reachable=reachable,
        reason=reason,
        is_fallback=is_fallback,
        # Rights are only known for a group that answered.
        is_admin=is_admin if reachable else None,
        missing_rights=tuple(missing),
    )


def actions(markup) -> list[str]:
    """Every callback action of a keyboard, row after row."""
    return [
        MenuCB.unpack(button.callback_data).action
        for row in markup.inline_keyboard
        for button in row
    ]


# --------------------------------------------------------------------------- #
# The summary line, in its three states
# --------------------------------------------------------------------------- #
def test_summary_reports_every_right_enabled() -> None:
    text = permissions_text(
        [make_check(-1001), make_check(-1002, categories=(IMAGES,))]
    )
    assert "✅ every right is enabled in all 2 groups" in text


def test_summary_reports_partial_rights() -> None:
    text = permissions_text(
        [
            make_check(-1001),
            make_check(-1002, categories=(IMAGES,), missing=("can_pin_messages",)),
        ]
    )
    assert "⚠️ partial — complete in 1 of 2" in text


def test_summary_reports_no_rights() -> None:
    text = permissions_text(
        [
            make_check(-1001, is_admin=False),
            make_check(-1002, categories=(IMAGES,), is_admin=False),
        ]
    )
    assert "⛔ enabled in none of the 2 groups" in text


# --------------------------------------------------------------------------- #
# Missing rights and what each one breaks
# --------------------------------------------------------------------------- #
def test_missing_rights_fit_on_the_group_line() -> None:
    text = permissions_text(
        [
            make_check(
                -1001,
                missing=(
                    "can_invite_users",
                    "can_restrict_members",
                    "can_delete_messages",
                    "can_pin_messages",
                ),
            )
        ]
    )
    # One line per group: the missing rights, with no repeated explanation under it.
    assert (
        "⚠️ 📄 Documents — <code>-1001</code> — missing: Invite users via link · "
        "Ban users · Delete messages · Pin messages" in text
    )
    assert "no join request arrives" not in text
    assert "cannot be removed" not in text
    assert "copy stays in the library" not in text
    assert "automatic promotion of the owner fails" not in text


# --------------------------------------------------------------------------- #
# An unreachable group
# --------------------------------------------------------------------------- #
def test_unreachable_group_still_renders_the_screen() -> None:
    text = permissions_text(
        [make_check(-1001, reachable=False, reason="chat not found")]
    )
    assert "⚠️ could not be checked (no reachable group)" in text
    assert "No storage group could be reached" in text
    assert "-1001" not in text


# --------------------------------------------------------------------------- #
# The keyboards
# --------------------------------------------------------------------------- #
def test_status_keyboard_opens_the_permissions_screen() -> None:
    markup = keyboards.status_kb()
    assert actions(markup) == [
        "status_check",
        "stats",
        "permissions",
        "pending",
        "back",
        "main",
    ]
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert keyboards.permissions_label() in labels


def test_permissions_keyboard_only_rechecks_and_goes_back() -> None:
    markup = keyboards.permissions_kb()
    assert actions(markup) == ["permissions_check", "back", "main"]
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert keyboards.permissions_check_label() in labels


# --------------------------------------------------------------------------- #
# The group ids moved from «General status» to «Statistics»
# --------------------------------------------------------------------------- #
def test_statistics_puts_each_count_next_to_its_group() -> None:
    stats = {"total": 1, "total_bytes": 10, "categories": {DOCUMENTS: 0, IMAGES: 1}}
    text = stats_text(
        stats, [make_check(-1001), make_check(-1002, categories=(IMAGES,))]
    )
    assert "✅ 📄 Documents: 0 — <code>-1001</code>" in text
    assert "✅ 🖼 Images: 1 — <code>-1002</code>" in text


class FakeServices:
    """Just enough of :class:`Services` for :func:`status_text`."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.bot = None
        self.pending = SimpleNamespace(total=0)
        self.files = SimpleNamespace(stats=self._stats)

    async def _stats(self):
        return {"total": 0, "total_bytes": 0, "categories": {}}

    def uptime_seconds(self) -> float:
        return 60.0


async def test_status_screen_no_longer_lists_the_groups(tmp_path) -> None:
    services = FakeServices(make_settings(tmp_path))
    text = await status_text(services, [make_check(-1001)])
    assert "-1001" not in text
    assert "✅ every right is enabled in all 1 group" in text

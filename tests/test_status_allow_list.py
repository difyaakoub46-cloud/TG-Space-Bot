"""The «المستخدمون المسموحون» line on «🩺 General status».

The bot refuses to start without ``ALLOWED_USER_IDS`` (see
``tests/test_access_guard.py``), so the screen never runs unconfigured in
practice. It still must not claim «الجميع»: an empty list is a missing setting,
and the screen says so instead of describing an open bot.
"""

from __future__ import annotations

from conftest import make_settings
from test_group_permissions import FakeServices, make_check
from src.bot.status import status_text
from src.core.i18n import Language, reset_language, set_language


async def _status(tmp_path, **overrides) -> str:
    services = FakeServices(make_settings(tmp_path, **overrides))
    return await status_text(services, [make_check(-1001)])


async def test_unconfigured_list_is_reported_as_missing(tmp_path) -> None:
    text = await _status(tmp_path, allowed_user_ids_raw="")
    assert "Allowed users: not configured" in text
    assert "everybody" not in text


async def test_unconfigured_list_reads_the_same_in_arabic(tmp_path) -> None:
    set_language(Language.ar)
    try:
        text = await _status(tmp_path, allowed_user_ids_raw="   ")
    finally:
        reset_language()
    assert "المستخدمون المسموحون: غير مهيّأ" in text
    assert "الجميع" not in text


async def test_configured_list_reports_how_many_accounts(tmp_path) -> None:
    text = await _status(tmp_path, allowed_user_ids_raw="111, 222")
    assert "Allowed users: 2" in text
    assert "not configured" not in text

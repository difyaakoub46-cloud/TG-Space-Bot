"""B4: the day and week filters follow the user's timezone, not UTC.

A file sent at 00:30 local time used to be filed under the UTC day that was still
running, so it was missing from «📅 Today» until UTC caught up. The window of a
period is now built from the local midnights and only then converted to the UTC
instants the query takes.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from src.services.files import history_date, history_window, local_zone

UTC = timezone.utc
# Three hours ahead of UTC, so a local midnight is 21:00 UTC the day before.
EAST = timezone(timedelta(hours=3))
# 22:30 UTC is already 01:30 on the next day in that zone.
NEAR_MIDNIGHT = datetime(2026, 11, 18, 22, 30, tzinfo=UTC)


def test_a_day_window_starts_at_the_local_midnight() -> None:
    start, end = history_window("day", date(2026, 11, 18), EAST)

    assert start == datetime(2026, 11, 17, 21, 0, tzinfo=UTC)
    assert end == datetime(2026, 11, 18, 21, 0, tzinfo=UTC)


def test_a_week_window_starts_at_the_local_monday() -> None:
    start, end = history_window("week", date(2026, 11, 18), EAST)

    assert start == datetime(2026, 11, 15, 21, 0, tzinfo=UTC)  # Monday 00:00 +03:00
    assert end == datetime(2026, 11, 22, 21, 0, tzinfo=UTC)


def test_a_file_after_local_midnight_belongs_to_the_new_day() -> None:
    start_18, end_18 = history_window("day", date(2026, 11, 18), EAST)
    start_19, end_19 = history_window("day", date(2026, 11, 19), EAST)

    # The file's own local day is the 19th, even though UTC still says the 18th.
    assert NEAR_MIDNIGHT.astimezone(EAST).date() == date(2026, 11, 19)
    assert start_19 <= NEAR_MIDNIGHT < end_19
    assert not start_18 <= NEAR_MIDNIGHT < end_18


def test_today_is_read_in_the_local_zone() -> None:
    assert history_date(None, EAST) == datetime.now(EAST).date()
    assert history_date(None, UTC) == datetime.now(UTC).date()


def test_without_a_zone_the_local_one_is_used() -> None:
    start, end = history_window("day", date(2026, 11, 18))

    assert start == datetime.combine(
        date(2026, 11, 18), time.min, tzinfo=local_zone()
    ).astimezone(UTC)
    assert end == datetime.combine(
        date(2026, 11, 19), time.min, tzinfo=local_zone()
    ).astimezone(UTC)


def test_all_time_has_no_window() -> None:
    assert history_window("all", date(2026, 11, 18), EAST) == (None, None)

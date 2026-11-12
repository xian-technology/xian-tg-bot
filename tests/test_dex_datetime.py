from datetime import UTC, datetime

from utils import parse_utc_datetime


def test_parse_utc_datetime_normalizes_z_naive_and_offsets() -> None:
    assert parse_utc_datetime("2026-04-23T12:00:00Z") == datetime(
        2026, 4, 23, 12, 0, tzinfo=UTC
    )
    assert parse_utc_datetime("2026-04-23T12:00:00") == datetime(
        2026, 4, 23, 12, 0, tzinfo=UTC
    )
    assert parse_utc_datetime("2026-04-23T14:00:00+02:00") == datetime(
        2026, 4, 23, 12, 0, tzinfo=UTC
    )

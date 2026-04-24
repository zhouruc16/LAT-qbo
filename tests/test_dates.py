from datetime import date
from tiktok_qbo.dates import parse_lat_date

def test_parse_lat_date_accepts_iso():
    assert parse_lat_date("2024-05-01") == date(2024, 5, 1)

def test_parse_lat_date_accepts_slashed():
    assert parse_lat_date("05/01/2024") == date(2024, 5, 1)

def test_parse_lat_date_accepts_datetime_obj():
    from datetime import datetime
    assert parse_lat_date(datetime(2024, 5, 1, 12, 0, 0)) == date(2024, 5, 1)

def test_parse_lat_date_returns_none_on_empty():
    assert parse_lat_date("") is None
    assert parse_lat_date(None) is None

def test_parse_lat_date_raises_on_garbage():
    import pytest
    with pytest.raises(ValueError):
        parse_lat_date("not a date")

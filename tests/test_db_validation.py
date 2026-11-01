import pytest

from app.db import accept_report

GOOD = dict(
    sender_hash="a" * 64,
    category="waste",
    latitude=41.3,
    longitude=69.2,
    photo_sha256="b" * 64,
    photo_path="x.jpg",
)


@pytest.mark.parametrize(
    "override",
    [
        {"sender_hash": "123456789"},  # сырой Telegram ID вместо псевдонима
        {"sender_hash": "A" * 64},  # не hex в нижнем регистре
        {"category": "lighting"},
        {"latitude": 95},
        {"longitude": -200},
        {"photo_sha256": "short"},
        {"photo_path": ""},
        {"max_photos": 0},
        {"merge_radius_m": 0},
    ],
)
def test_invalid_input_is_rejected_before_touching_database(override):
    # conn=None: если проверка не сработает, тест упадёт при обращении к базе.
    with pytest.raises(ValueError):
        accept_report(None, **{**GOOD, **override})

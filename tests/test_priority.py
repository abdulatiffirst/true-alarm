from datetime import datetime, timedelta, timezone

import pytest

from app.domain.priority import Priority, PriorityConfig, compute_priority

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
CONFIG = PriorityConfig(age_thresholds_hours=(24, 72, 168), sender_thresholds=(3, 6, 11))

H = timedelta(hours=1)
S = timedelta(seconds=1)

# Таблица из ТЗ, записанная независимо от кода.
# Ключ: число отправителей, значения: цвета для 4 возрастных столбцов.
EXPECTED = {
    1: ["green", "yellow", "orange", "red"],
    2: ["green", "yellow", "orange", "red"],
    3: ["yellow", "orange", "red", "red"],
    5: ["yellow", "orange", "red", "red"],
    6: ["orange", "red", "red", "red"],
    10: ["orange", "red", "red", "red"],
    11: ["red", "red", "red", "red"],
    100: ["red", "red", "red", "red"],
}

# Возраст и номер столбца, в который он должен попасть (включая границы).
AGES = [
    (timedelta(0), 0),
    (24 * H - S, 0),
    (24 * H, 1),
    (72 * H - S, 1),
    (72 * H, 2),
    (168 * H - S, 2),
    (168 * H, 3),
    (timedelta(days=30), 3),
]


@pytest.mark.parametrize("senders", list(EXPECTED))
@pytest.mark.parametrize("age,column", AGES)
def test_priority_matches_table(senders, age, column):
    result = compute_priority(NOW - age, senders, NOW, CONFIG)
    assert result.value == EXPECTED[senders][column]


def test_returns_priority_enum():
    assert compute_priority(NOW, 1, NOW, CONFIG) is Priority.GREEN


def test_negative_age_is_treated_as_zero():
    future_first_report = NOW + timedelta(minutes=5)
    assert compute_priority(future_first_report, 1, NOW, CONFIG) is Priority.GREEN


def test_naive_datetimes_are_rejected():
    naive = datetime(2026, 10, 29, 12, 0)
    with pytest.raises(ValueError):
        compute_priority(naive, 1, NOW, CONFIG)
    with pytest.raises(ValueError):
        compute_priority(NOW, 1, naive, CONFIG)


def test_zero_senders_rejected():
    with pytest.raises(ValueError):
        compute_priority(NOW, 0, NOW, CONFIG)


def test_thresholds_can_be_changed_via_config():
    custom = PriorityConfig(age_thresholds_hours=(1, 2, 3), sender_thresholds=(2, 3, 4))
    assert compute_priority(NOW - 1 * H, 1, NOW, custom) is Priority.YELLOW


@pytest.mark.parametrize(
    "ages,senders",
    [
        ((24, 24, 168), (3, 6, 11)),  # не строго возрастают
        ((24, 72), (3, 6, 11)),  # не 3 значения
        ((0, 72, 168), (3, 6, 11)),  # ноль
        ((24, 72, 168), (6, 3, 11)),  # убывают
    ],
)
def test_invalid_config_rejected(ages, senders):
    with pytest.raises(ValueError):
        PriorityConfig(age_thresholds_hours=ages, sender_thresholds=senders)
"""Приоритет по сообщениям и времени.

Это НЕ объективный уровень опасности: цвет зависит только от числа
независимых отправителей и от того, как давно поступила первая заявка.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


class Priority(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


_G, _Y, _O, _R = Priority.GREEN, Priority.YELLOW, Priority.ORANGE, Priority.RED

# Таблица приоритета. Это политика проекта, а не числовые пороги.
# Строки: число независимых отправителей (1–2, 3–5, 6–10, 11 и больше).
# Столбцы: возраст проблемы (< 24 ч, 24–72 ч, 72–168 ч, >= 168 ч).
PRIORITY_MATRIX: tuple[tuple[Priority, ...], ...] = (
    (_G, _Y, _O, _R),
    (_Y, _O, _R, _R),
    (_O, _R, _R, _R),
    (_R, _R, _R, _R),
)


@dataclass(frozen=True)
class PriorityConfig:
    """Пороги. Значения приходят из настроек (app/config.py)."""

    # Нижние границы столбцов 2, 3 и 4: 24, 72 и 168 часов.
    age_thresholds_hours: tuple[int, int, int]
    # Нижние границы строк 2, 3 и 4: 3, 6 и 11 отправителей.
    sender_thresholds: tuple[int, int, int]

    def __post_init__(self) -> None:
        for name, values in (
            ("age_thresholds_hours", self.age_thresholds_hours),
            ("sender_thresholds", self.sender_thresholds),
        ):
            if len(values) != 3:
                raise ValueError(f"{name}: нужно ровно 3 значения")
            if values[0] <= 0 or not (values[0] < values[1] < values[2]):
                raise ValueError(f"{name}: значения должны быть положительными и строго возрастать")


def compute_priority(
    first_report_at: datetime,
    senders_count: int,
    now: datetime,
    config: PriorityConfig,
) -> Priority:
    """Цвет проблемы. Все даты обязаны быть с часовым поясом (в проекте это UTC)."""
    if first_report_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("Даты должны содержать часовой пояс (UTC)")
    if senders_count < 1:
        raise ValueError("У проблемы должен быть минимум один отправитель")

    # Если часы сервера и базы немного разошлись, возраст не может быть отрицательным.
    age = max(now - first_report_at, timedelta(0))

    age_band = sum(1 for h in config.age_thresholds_hours if age >= timedelta(hours=h))
    sender_band = sum(1 for n in config.sender_thresholds if senders_count >= n)
    return PRIORITY_MATRIX[sender_band][age_band]
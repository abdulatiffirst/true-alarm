
from datetime import datetime, timedelta, timezone

from app.domain.priority import (
    Priority,
    PriorityConfig,
    compute_priority,
)

config = PriorityConfig(
    age_thresholds_hours=(24, 72, 168),
    sender_thresholds=(3, 6, 11),
)

now = datetime.now(timezone.utc)

tests = [
    # Отправители, возраст заявки, ожидаемый приоритет
    (1, 1, Priority.GREEN),
    (1, 24, Priority.YELLOW),
    (1, 72, Priority.ORANGE),
    (1, 168, Priority.RED),
    (3, 1, Priority.YELLOW),
    (3, 24, Priority.ORANGE),
    (6, 1, Priority.ORANGE),
    (11, 1, Priority.RED),
]

for senders, age_hours, expected in tests:
    first_report_at = now - timedelta(hours=age_hours)

    actual = compute_priority(
        first_report_at=first_report_at,
        senders_count=senders,
        now=now,
        config=config,
    )

    status = "OK" if actual == expected else "FAIL"

    print(
        f"{status}: отправителей={senders}, "
        f"возраст={age_hours} ч, "
        f"ожидалось={expected.value}, "
        f"получено={actual.value}"
    )

    assert actual == expected, (
        f"Ошибка: ожидалось {expected.value}, "
        f"получено {actual.value}"
    )

print("\nВсе тесты приоритета пройдены!")


from app.db import find_nearby_problems

problems = find_nearby_problems(
    latitude=41.2995,
    longitude=69.2401,
    radius_meters=30,
)

print(f"Найдено проблем: {len(problems)}")

for problem in problems:
    print(
        problem["title"],
        f'{problem["distance_meters"]:.1f} м',
        problem["status"],
    )

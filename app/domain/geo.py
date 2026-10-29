from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000


def validate_coordinates(lat: float, lon: float) -> None:
    """Проверяет, что широта и долгота допустимы."""
    if not (-90 <= lat <= 90):
        raise ValueError("Широта должна быть от -90 до 90")
    if not (-180 <= lon <= 180):
        raise ValueError("Долгота должна быть от -180 до 180")


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками по формуле гаверсинуса, в метрах."""
    validate_coordinates(lat1, lon1)
    validate_coordinates(lat2, lon2)
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))
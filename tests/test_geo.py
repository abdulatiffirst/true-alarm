import pytest

from app.domain.geo import distance_m, validate_coordinates


def test_same_point_is_zero():
    assert distance_m(41.3111, 69.2797, 41.3111, 69.2797) == 0


def test_one_degree_of_latitude():
    assert distance_m(0, 0, 1, 0) == pytest.approx(111_195, abs=5)


def test_small_shift_is_about_33_meters():
    # 0.0003 градуса широты примерно 33 м
    assert distance_m(41.3111, 69.2797, 41.3114, 69.2797) == pytest.approx(33.4, abs=0.5)


def test_distance_is_symmetric():
    a = distance_m(41.31, 69.27, 41.32, 69.29)
    b = distance_m(41.32, 69.29, 41.31, 69.27)
    assert a == pytest.approx(b)


@pytest.mark.parametrize("lat,lon", [(91, 0), (-91, 0), (0, 181), (0, -181)])
def test_invalid_coordinates_rejected(lat, lon):
    with pytest.raises(ValueError):
        validate_coordinates(lat, lon)
    with pytest.raises(ValueError):
        distance_m(lat, lon, 0, 0)
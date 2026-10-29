from app.config import Settings, get_priority_config


def test_default_priority_thresholds():
    cfg = get_priority_config()
    assert cfg.age_thresholds_hours == (24, 72, 168)
    assert cfg.sender_thresholds == (3, 6, 11)


def test_thresholds_can_be_overridden_from_env(monkeypatch):
    monkeypatch.setenv("PRIORITY_AGE_THRESHOLDS_HOURS", "[1, 2, 3]")
    monkeypatch.setenv("MERGE_RADIUS_M", "50")
    s = Settings(_env_file=None)
    assert s.priority_age_thresholds_hours == (1, 2, 3)
    assert s.merge_radius_m == 50
import pytest

from app.domain.pseudonym import pseudonymize_telegram_id

SECRET = "test-secret-1234567890"


def test_known_value():
    # Фиксируем алгоритм: случайное изменение формулы сломает этот тест.
    assert pseudonymize_telegram_id(123456789, SECRET) == (
        "abbf79bd6417f4616040f9f93ab9183036e13f65aaf397729764b0255aac8688"
    )


def test_is_stable():
    assert pseudonymize_telegram_id(42, SECRET) == pseudonymize_telegram_id(42, SECRET)


def test_different_ids_give_different_pseudonyms():
    assert pseudonymize_telegram_id(1, SECRET) != pseudonymize_telegram_id(2, SECRET)


def test_different_secrets_give_different_pseudonyms():
    assert pseudonymize_telegram_id(1, SECRET) != pseudonymize_telegram_id(1, SECRET + "x")


def test_does_not_contain_id_and_has_fixed_length():
    result = pseudonymize_telegram_id(123456789, SECRET)
    assert "123456789" not in result
    assert len(result) == 64


def test_short_secret_rejected():
    with pytest.raises(ValueError):
        pseudonymize_telegram_id(1, "short")
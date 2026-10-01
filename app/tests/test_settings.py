import pytest

from app.config import Settings


def test_dev_token_refused_in_production():
    with pytest.raises(ValueError):
        Settings(app_env="production", dev_bearer_token="y" * 32)


def test_short_dev_token_refused():
    with pytest.raises(ValueError):
        Settings(app_env="development", dev_bearer_token="short")


def test_dev_token_disabled_by_default():
    assert Settings().dev_token_enabled is False

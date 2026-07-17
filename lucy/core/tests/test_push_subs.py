"""Round-trip tests for push_subs.py — the per-person Web Push subscription
store. Pure logic, no network."""
import pytest

from lucy.core import config, push_subs


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PUSH_SUBSCRIPTIONS_PATH", tmp_path / "push_subscriptions.json")


def test_get_subscription_missing_returns_none():
    # Act
    result = push_subs.get_subscription("Jeff")
    # Assert
    assert result is None


def test_set_then_get_round_trips():
    # Arrange
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "x", "auth": "y"}}
    # Act
    push_subs.set_subscription("Jeff", sub)
    result = push_subs.get_subscription("Jeff")
    # Assert
    assert result == sub


def test_remove_subscription_deletes_and_reports_true():
    # Arrange
    push_subs.set_subscription("Jeff", {"endpoint": "https://push.example/abc"})
    # Act
    removed = push_subs.remove_subscription("Jeff")
    # Assert
    assert removed is True
    assert push_subs.get_subscription("Jeff") is None


def test_remove_subscription_missing_returns_false():
    # Act
    removed = push_subs.remove_subscription("Nobody")
    # Assert
    assert removed is False


def test_set_subscription_does_not_clobber_other_people():
    # Arrange
    push_subs.set_subscription("Jeff", {"endpoint": "https://push.example/jeff"})
    # Act
    push_subs.set_subscription("Sam", {"endpoint": "https://push.example/sam"})
    # Assert
    assert push_subs.get_subscription("Jeff")["endpoint"] == "https://push.example/jeff"
    assert push_subs.get_subscription("Sam")["endpoint"] == "https://push.example/sam"

"""notify_webpush plugin tests — mocks pywebpush.webpush, no real network
calls. Covers the loud-failure contract and the 410 self-heal.

Uses asyncio.run() directly rather than pytest-asyncio, which isn't a
dependency of this project.
"""
import asyncio

import pytest

from lucy.core import config, push_subs, settings
from lucy.plugins.notify_webpush import plugin as webpush_plugin
from pywebpush import WebPushException


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PUSH_SUBSCRIPTIONS_PATH", tmp_path / "push_subscriptions.json")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")


@pytest.fixture
def plugin():
    return webpush_plugin.Plugin(ctx=None)


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def test_push_without_subscription_raises_and_skips_network(plugin, monkeypatch):
    # Arrange
    called = False
    def fake_webpush(**kwargs):
        nonlocal called
        called = True
    monkeypatch.setattr(webpush_plugin, "webpush", fake_webpush)

    # Act / Assert
    with pytest.raises(RuntimeError, match="no push subscription"):
        asyncio.run(plugin.call("push", to="Jeff", title="t", message="m"))
    assert called is False


def test_push_without_vapid_keys_raises(plugin):
    # Arrange
    push_subs.set_subscription("Jeff", {"endpoint": "https://push.example/jeff",
                                        "keys": {"p256dh": "x", "auth": "y"}})

    # Act / Assert
    with pytest.raises(RuntimeError, match="no VAPID keys"):
        asyncio.run(plugin.call("push", to="Jeff", title="t", message="m"))


def test_push_success_returns_pushed(plugin, monkeypatch):
    # Arrange
    push_subs.set_subscription("Jeff", {"endpoint": "https://push.example/jeff",
                                        "keys": {"p256dh": "x", "auth": "y"}})
    settings.save(settings.new_vapid_keys())
    monkeypatch.setattr(webpush_plugin, "webpush", lambda **kwargs: None)

    # Act
    result = asyncio.run(plugin.call("push", to="Jeff", title="t", message="m"))

    # Assert
    assert result == {"pushed": True, "to": "Jeff"}


def test_push_410_self_heals_subscription(plugin, monkeypatch):
    # Arrange
    push_subs.set_subscription("Jeff", {"endpoint": "https://push.example/jeff",
                                        "keys": {"p256dh": "x", "auth": "y"}})
    settings.save(settings.new_vapid_keys())

    def raise_410(**kwargs):
        raise WebPushException("gone", response=FakeResponse(410))
    monkeypatch.setattr(webpush_plugin, "webpush", raise_410)

    # Act / Assert
    with pytest.raises(RuntimeError, match="push to Jeff failed"):
        asyncio.run(plugin.call("push", to="Jeff", title="t", message="m"))
    assert push_subs.get_subscription("Jeff") is None


def test_push_non_410_error_keeps_subscription(plugin, monkeypatch):
    # Arrange
    push_subs.set_subscription("Jeff", {"endpoint": "https://push.example/jeff",
                                        "keys": {"p256dh": "x", "auth": "y"}})
    settings.save(settings.new_vapid_keys())

    def raise_500(**kwargs):
        raise WebPushException("server error", response=FakeResponse(500))
    monkeypatch.setattr(webpush_plugin, "webpush", raise_500)

    # Act / Assert
    with pytest.raises(RuntimeError, match="push to Jeff failed"):
        asyncio.run(plugin.call("push", to="Jeff", title="t", message="m"))
    assert push_subs.get_subscription("Jeff") is not None

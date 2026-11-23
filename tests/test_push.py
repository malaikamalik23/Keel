"""Push notifications: only real push services are contacted, each message is encrypted for its device (the relay sees
ciphertext), dead devices are forgotten, and reminders reach devices even when email isn't set up or fails."""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.api.main import create_app
from app.config import Settings
from app.db.models import Notification, PushSubscription, Reminder
from app.integrations.push import CombinedNotifier, PushError, PushNotifier, check_endpoint, check_keys, public_key
from tests.fakes import ScriptedLLM, local_client

TZ = ZoneInfo("Asia/Karachi")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _device():
    """A browser's push keys: its own key pair and a 16-byte auth secret."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return priv, _b64(pub), _b64(auth), auth


@pytest.mark.parametrize("endpoint", [
    "https://fcm.googleapis.com/fcm/send/abc", "https://web.push.apple.com/QGx", "https://updates.push.services.mozilla.com/wpush/v2/x",
    "https://wns2-par02p.notify.windows.com/w/?token=x",
])
def test_real_push_services_are_accepted(endpoint):
    assert check_endpoint(endpoint) == endpoint


@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/fcm/send/abc",           # not https
    "https://127.0.0.1/push", "https://localhost/x",     # this computer
    "https://evil.example/fcm.googleapis.com",           # the name in the path
    "https://fcm.googleapis.com.evil.example/x",         # the name as a prefix
    "https://user@fcm.googleapis.com/x",                 # credentials in the address
    "https://fcm.googleapis.com:8443/x",                 # another port
    "",
])
def test_anything_else_is_refused(endpoint):
    with pytest.raises(PushError):
        check_endpoint(endpoint)


def test_device_keys_are_checked():
    _, p256dh, auth, _ = _device()
    assert check_keys(p256dh, auth) == (p256dh, auth)
    for bad in (("abc", auth), (p256dh, "abc"), (p256dh[:-4], auth)):
        with pytest.raises(PushError):
            check_keys(*bad)


def _app(tmp_path, **kw):
    s = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'k.db').as_posix()}", anthropic_api_key=None,
                 agent_timezone="Asia/Karachi", push_key_path=str(tmp_path / "data" / "push_vapid.pem"), **kw)
    app = create_app(s, ScriptedLLM([]))
    return app, local_client(app)


def _push(app) -> PushNotifier:
    n = app.state.container.notifier
    return next(p for p in n.parts if isinstance(p, PushNotifier))


def test_subscribing_a_device_from_the_dashboard(tmp_path):
    app, client = _app(tmp_path)
    info = client.get("/api/push").json()
    key_file = tmp_path / "data" / "push_vapid.pem"
    assert info["enabled"] and info["devices"] == [] and key_file.exists()
    assert len(base64.urlsafe_b64decode(info["public_key"] + "==")) == 65  # what the browser needs to subscribe
    assert client.get("/api/push").json()["public_key"] == info["public_key"]  # the key is created once, then kept
    if os.name != "nt":
        assert key_file.stat().st_mode & 0o777 == 0o600

    _, p256dh, auth, _ = _device()
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/device-1", "keys": {"p256dh": p256dh, "auth": auth}}
    assert client.post("/api/push/subscribe", json={"subscription": sub, "label": "Android phone · Chrome"}).status_code == 201
    assert client.post("/api/push/subscribe", json={"subscription": sub, "label": "renamed"}).status_code == 201  # same device: updated
    devices = client.get("/api/push").json()["devices"]
    assert [d["label"] for d in devices] == ["renamed"] and _push(app).devices() == 1
    evil = {**sub, "endpoint": "https://127.0.0.1:8000/api/tasks"}
    assert client.post("/api/push/subscribe", json={"subscription": evil}).status_code == 422
    assert client.delete(f"/api/push/devices/{devices[0]['id']}").status_code == 204 and _push(app).devices() == 0
    assert client.post("/api/push/test").status_code == 409  # no device left


def test_a_message_is_encrypted_for_the_device_and_signed_by_keel(tmp_path, monkeypatch):
    import http_ece

    app, client = _app(tmp_path)
    priv, p256dh, auth, auth_raw = _device()
    client.post("/api/push/subscribe", json={"subscription": {"endpoint": "https://fcm.googleapis.com/fcm/send/d1",
                                                              "keys": {"p256dh": p256dh, "auth": auth}}})
    sent = []

    class Resp:
        status_code = 201
        text = ""
        headers: dict = {}

    def fake_post(url, data=None, headers=None, timeout=None, **_):
        sent.append((url, data, headers))
        return Resp()

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.Session.post", lambda self, *a, **kw: fake_post(*a, **kw), raising=False)
    push = _push(app)
    push._background = False
    assert push.send("Reminder: call the bank at 6pm") == 1
    url, body, headers = sent[0]
    assert url == "https://fcm.googleapis.com/fcm/send/d1" and b"call the bank" not in body  # the relay sees ciphertext
    assert headers["Content-Encoding"] == "aes128gcm" and headers["Authorization"].startswith("vapid t=")
    assert public_key(tmp_path / "data" / "push_vapid.pem") in headers["Authorization"]
    plain = json.loads(http_ece.decrypt(body, private_key=priv, auth_secret=auth_raw, version="aes128gcm"))
    assert plain == {"title": "Keel", "body": "Reminder: call the bank at 6pm", "url": "/"}


def test_a_device_that_unsubscribed_is_forgotten(tmp_path):
    app, client = _app(tmp_path)
    _, p256dh, auth, _ = _device()
    client.post("/api/push/subscribe", json={"subscription": {"endpoint": "https://fcm.googleapis.com/fcm/send/gone",
                                                              "keys": {"p256dh": p256dh, "auth": auth}}})
    push = _push(app)
    push._send, push._background = (lambda info, payload: 410), False
    assert push.send("hello") == 0
    with app.state.container.session_factory() as db:
        assert db.query(PushSubscription).count() == 0
    assert push.devices() == 0


def test_reminders_reach_devices_even_when_email_fails(tmp_path):
    from app.integrations.notifier import NotificationError

    class BrokenEmail:
        channel = "email"

        def send(self, text):
            raise NotificationError("mail server down")

    got = []
    push = type("P", (), {"channel": "push", "send": lambda self, text: got.append(text) or 1})()
    combined = CombinedNotifier(BrokenEmail(), push)
    combined.send("Reminder: Submit the form")
    assert got == ["Reminder: Submit the form"] and combined.channel == "email+push"
    with pytest.raises(NotificationError, match="mail server down"):
        CombinedNotifier(BrokenEmail()).send("x")  # nothing delivered anywhere: the failure is reported

    # end to end: the scheduler sends a due reminder to a subscribed device
    app, client = _app(tmp_path)
    _, p256dh, auth, _ = _device()
    client.post("/api/push/subscribe", json={"subscription": {"endpoint": "https://web.push.apple.com/QGx",
                                                              "keys": {"p256dh": p256dh, "auth": auth}}})
    delivered = []
    p = _push(app)
    p._send, p._background = (lambda info, payload: delivered.append(json.loads(payload)) or 201), False
    with app.state.container.session_factory() as db:
        db.add(Reminder(text="Submit the form", fire_at=datetime(2026, 11, 23, 4, 59)))
        db.commit()
    app.state.container.scheduler.tick(datetime(2026, 11, 23, 10, 0, tzinfo=TZ))
    assert delivered and delivered[0]["body"] == "Reminder: Submit the form"
    with app.state.container.session_factory() as db:
        assert db.query(Notification).filter_by(kind="reminder").one().channel == "push"


def test_a_localhost_contact_is_replaced_so_apple_accepts_the_messages():
    from app.integrations.push import DEFAULT_CONTACT, usable_contact

    assert usable_contact("mailto:keel@localhost") == DEFAULT_CONTACT  # Apple: 403 BadJwtToken
    assert usable_contact("mailto:me@laptop") == DEFAULT_CONTACT and usable_contact("") == DEFAULT_CONTACT
    assert usable_contact("https://localhost:8000") == DEFAULT_CONTACT and usable_contact("keel@example.com") == DEFAULT_CONTACT
    assert usable_contact("mailto:me@uni.edu.pk") == "mailto:me@uni.edu.pk"
    assert usable_contact("https://keel.example.org") == "https://keel.example.org"
    from app.config import Settings

    assert usable_contact(Settings.model_fields["push_contact"].default) == Settings.model_fields["push_contact"].default

"""Phones with the AlvaOS app: pairing with a QR code, the Devices list (files_server)."""

import json
from urllib.parse import parse_qs, urlparse

import files_server as fs
import hub_photos_sync
from test_files_server import H, client, sign_in  # noqa: F401  (the fixture)


def pair_link(c):
    r = c.post("/api/devices/pair-code", json={"origin": "http://192.168.1.20:8090"}, headers=H)
    assert r.status_code == 200
    return r.get_json()


def phone(c=None):
    """A second client: the phone, with no cookie of the browser."""
    return fs.app.test_client()


def test_a_qr_code_signs_a_phone_in_once(client):  # noqa: F811
    sign_in(client, "anna", "anna-pass")
    data = pair_link(client)
    q = parse_qs(urlparse(data["link"]).query)
    assert data["link"].startswith("alvaos://pair?") and q["a"] == ["http://192.168.1.20:8090"] and q["u"] == ["anna"]
    code = q["c"][0]
    assert len(code) == 8 and data["code"] == f"{code[:4]}-{code[4:]}"
    p = phone()
    r = p.post("/api/devices/pair", json={"code": data["code"].lower(),
                                          "device": {"name": "Pixel 8", "model": "Pixel 8", "app_version": "beta-v0.1.0"}})
    assert r.status_code == 200
    token = r.get_json()["token"]
    # The phone uses its own session, as a cookie, for everything the Hub offers.
    p.set_cookie(fs.COOKIE, token)
    assert p.get("/api/me").get_json()["user"] == "anna"
    # Used once: a second phone cannot take the same code.
    again = phone().post("/api/devices/pair", json={"code": code, "device": {"name": "Other"}})
    assert again.status_code == 401


def test_a_new_qr_code_makes_the_last_one_useless(client):  # noqa: F811
    sign_in(client, "anna", "anna-pass")
    first = pair_link(client)["code"]
    pair_link(client)
    assert phone().post("/api/devices/pair", json={"code": first}).status_code == 401


def test_an_old_code_does_not_work(client, monkeypatch):  # noqa: F811
    sign_in(client, "anna", "anna-pass")
    code = pair_link(client)["code"]
    later = fs.time.time() + fs.PAIR_CODE_SECONDS + 5
    monkeypatch.setattr(fs.time, "time", lambda: later)
    assert phone().post("/api/devices/pair", json={"code": code}).status_code == 401


def test_devices_are_listed_renamed_and_signed_out(client, monkeypatch):  # noqa: F811
    sign_in(client, "anna", "anna-pass")
    p = phone()
    token = p.post("/api/devices/pair", json={"code": pair_link(client)["code"],
                                              "device": {"name": "Pixel 8\n", "model": "Pixel 8"}}).get_json()["token"]
    p.set_cookie(fs.COOKIE, token)
    dev_id = devices(client)[0]["id"]
    monkeypatch.setattr(hub_photos_sync, "phones_of", lambda session: [
        {"device": dev_id, "albums": ["Camera"], "count": 12, "last_sync": "2026-10-07T10:00:00"}])
    listed = devices(client)
    assert len(listed) == 1 and listed[0]["name"] == "Pixel 8" and not listed[0]["this"]
    assert listed[0]["backup"] == {"albums": ["Camera"], "count": 12, "last_sync": "2026-10-07T10:00:00"}
    assert devices(p)[0]["this"]                 # the phone sees itself as "this device"
    dev = listed[0]["id"]
    assert client.patch(f"/api/devices/{dev}", json={"name": "Anna's phone"}, headers=H).status_code == 200
    assert devices(client)[0]["name"] == "Anna's phone"
    # Ben does not see Anna's phone and cannot remove it.
    ben = phone()
    sign_in(ben, "ben", "ben-pass")
    assert devices(ben) == [] and ben.delete(f"/api/devices/{dev}", headers=H).status_code == 404
    assert client.delete(f"/api/devices/{dev}", headers=H).status_code == 200
    assert p.get("/api/me").status_code == 401   # signed out
    assert devices(client) == []


def test_signing_in_with_a_password_from_the_app_makes_a_device(client):  # noqa: F811
    p = phone()
    r = p.post("/api/login", json={"username": "anna", "password": "anna-pass", "device": {"name": "Galaxy"}})
    assert r.status_code == 200 and r.get_json()["token"]
    sign_in(client, "anna", "anna-pass")
    assert [d["name"] for d in devices(client)] == ["Galaxy"]
    # A browser gets no token in the answer, only the cookie.
    assert "token" not in sign_in(phone(), "anna", "anna-pass").get_json()


def test_a_new_password_signs_the_phones_out(client, tmp_path):  # noqa: F811
    sign_in(client, "anna", "anna-pass")
    p = phone()
    p.set_cookie(fs.COOKIE, p.post("/api/devices/pair", json={"code": pair_link(client)["code"]}).get_json()["token"])
    users = json.loads((tmp_path / "users.json").read_text())
    users["anna"]["files_auth"] = {"changed": True}
    (tmp_path / "users.json").write_text(json.dumps(users))
    assert p.get("/api/me").status_code == 401


def devices(c):
    r = c.get("/api/devices")
    assert r.status_code == 200
    return r.get_json()["devices"]


def test_the_qr_code_carries_the_link_address_and_a_removed_phone_loses_it(client, monkeypatch):  # noqa: F811
    import link_client
    removed = []
    monkeypatch.setattr(link_client, "node_id", lambda: "ab" * 32)
    monkeypatch.setattr(link_client, "remove_device", lambda device: removed.append(device) or True)
    sign_in(client, "anna", "anna-pass")
    data = pair_link(client)
    assert parse_qs(urlparse(data["link"]).query)["l"] == ["ab" * 32] and data["away"] is True
    p = phone()
    assert p.post("/api/devices/pair", json={"code": data["code"], "device": {"name": "Pixel"}}).status_code == 200
    dev = client.get("/api/devices").get_json()["devices"][0]["id"]
    assert client.delete(f"/api/devices/{dev}", headers=H).status_code == 200
    assert removed == [dev]
    # Link off (or not running): no address in the code, the app then works at home only.
    monkeypatch.setattr(link_client, "node_id", lambda: "")
    off = pair_link(client)
    assert "l" not in parse_qs(urlparse(off["link"]).query) and off["away"] is False


def test_a_phone_that_paired_at_home_registers_its_link_key(client, monkeypatch):  # noqa: F811
    import link_client
    added = []
    monkeypatch.setattr(link_client, "node_id", lambda: "ab" * 32)
    monkeypatch.setattr(link_client, "add_phone", lambda key, dev, name, user: added.append((key, name, user)) or True)
    sign_in(client, "anna", "anna-pass")
    assert client.post("/api/devices/link", json={"key": "cd" * 32}, headers=H).status_code == 403    # a browser, not a phone
    p = phone()
    p.set_cookie(fs.COOKIE, p.post("/api/devices/pair", json={"code": pair_link(client)["code"], "device": {"name": "Pixel"}}).get_json()["token"])
    assert p.post("/api/devices/link", json={"key": "nonsense"}, headers=H).status_code == 400
    r = p.post("/api/devices/link", json={"key": "CD" * 32}, headers=H)
    assert r.status_code == 200 and r.get_json() == {"success": True, "link": "ab" * 32}
    assert added == [("cd" * 32, "Pixel", "anna")]

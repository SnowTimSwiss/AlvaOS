"""Problems by email and Telegram in the background (backend/alert_delivery.py)."""

import os
import smtplib
import stat
from datetime import datetime

import alert_delivery as ad

DISK = {"id": "smart-sdb", "severity": "critical", "title": "Disk sdb is failing", "message": "Replace it."}
CPU = {"id": "cpu-usage-critical", "severity": "critical", "title": "CPU usage is critical", "message": "99%"}
INFO = {"id": "x", "severity": "info", "title": "fyi", "message": ""}


def run(tmp_path, alerts, sent_log, now=datetime(2026, 10, 1, 12, 0)):   # a Thursday
    def send(state, subject, body):
        sent_log.append((subject, body))
        return ["email"]
    return ad.run_once(collect=lambda: alerts, now=now, path=str(tmp_path / "d.json"), send=send,
                       pools=lambda: [{"name": "main", "used_percent": 41}])


def test_a_problem_is_sent_once_when_it_lasts(tmp_path):
    log = []
    run(tmp_path, [DISK, CPU, INFO], log)
    assert log == []                                   # first sighting: could be a blip
    run(tmp_path, [DISK, CPU, INFO], log)
    assert len(log) == 1 and "Disk sdb is failing" in log[0][0]
    assert "CPU" not in log[0][1]                      # load spikes are not sent
    run(tmp_path, [DISK], log)
    assert len(log) == 1                               # not again while it is there


def test_a_problem_that_comes_back_is_sent_again(tmp_path):
    log = []
    for alerts in ([DISK], [DISK], [], [DISK], [DISK]):
        run(tmp_path, alerts, log)
    assert len(log) == 2


def test_a_blip_is_never_sent(tmp_path):
    log = []
    for alerts in ([DISK], [], [DISK], []):
        run(tmp_path, alerts, log)
    assert log == []


def test_nothing_is_marked_sent_without_a_channel(tmp_path):
    path = str(tmp_path / "d.json")
    for _ in range(2):
        state = ad.run_once(collect=lambda: [DISK], now=datetime(2026, 10, 1, 12), path=path,
                            send=lambda s, a, b: [], pools=lambda: [])
    assert state["sent"] == []


def test_weekly_report_on_sunday_morning_once(tmp_path):
    log = []
    sunday = datetime(2026, 10, 4, 10, 30)
    run(tmp_path, [], log, now=datetime(2026, 10, 4, 9, 0))
    assert log == []
    run(tmp_path, [], log, now=sunday)
    assert len(log) == 1 and "all is well" in log[0][0] and "main: 41% used" in log[0][1]
    run(tmp_path, [], log, now=datetime(2026, 10, 4, 15, 0))
    assert len(log) == 1


def test_weekly_report_names_problems_and_can_be_off(tmp_path):
    subject, body = ad.report_message([DISK], [], "alva-home")
    assert "1 thing to look at" in subject and "Disk sdb" in body
    state = ad.load_state(str(tmp_path / "x.json"))
    state["report"]["weekly"] = False
    assert ad.report_due(state, datetime(2026, 10, 4, 11)) is False


def test_settings_file_is_private_and_hides_the_password(tmp_path):
    path = str(tmp_path / "d.json")
    state = ad.load_state(path)
    email, problem = ad.apply_email_settings(state, {"provider": "gmail", "recipient": "tim@example.com",
                                                     "password": "app-pass", "enabled": True})
    assert problem == ""
    assert (email["host"], email["port"], email["security"], email["username"]) == \
        ("smtp.gmail.com", 587, "starttls", "tim@example.com")
    state["email"] = email
    ad.save_state(state, path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    public = ad.public_settings(ad.load_state(path))
    assert "password" not in public["email"] and public["email"]["password_set"] is True
    # Saving again without a password keeps the old one.
    again, _ = ad.apply_email_settings(ad.load_state(path), {"recipient": "tim@example.com"})
    assert again["password"] == "app-pass"


def test_bad_addresses_and_hosts_are_refused(tmp_path):
    state = ad.load_state(str(tmp_path / "d.json"))
    assert ad.apply_email_settings(state, {"enabled": True, "recipient": "nope", "host": "smtp.x.ch"})[1]
    assert ad.apply_email_settings(state, {"enabled": True, "recipient": "a@b.ch", "host": "bad host;"})[1]
    assert ad.apply_email_settings(state, {"enabled": True, "recipient": "a@b.ch\nBcc: x@y.ch",
                                           "host": "smtp.x.ch"})[1]
    assert ad.apply_email_settings(state, {"provider": "evil"})[1]


class FakeSMTP:
    sent = []
    fail_login = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        self.tls = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"no")

    def send_message(self, msg):
        FakeSMTP.sent.append((self.tls, msg["To"], msg["Subject"]))


class FakeModule:
    SMTP = FakeSMTP
    SMTP_SSL = FakeSMTP


def test_email_is_sent_with_starttls_and_login_errors_are_explained():
    email = {"host": "smtp.x.ch", "port": 587, "security": "starttls", "username": "u", "password": "p",
             "sender": "", "recipient": "tim@example.com"}
    assert ad.send_email(email, "Disk\r\nBcc: evil@x.ch", "body", smtp_module=FakeModule) == (True, "")
    assert FakeSMTP.sent[-1] == (True, "tim@example.com", "Disk Bcc: evil@x.ch")
    FakeSMTP.fail_login = True
    ok, error = ad.send_email(email, "s", "b", smtp_module=FakeModule)
    FakeSMTP.fail_login = False
    assert not ok and "app password" in error

"""Calendar and Chat in the Hub (hub_calendar.py, hub_chat.py, hub_data.py)."""

import io
import json

import pytest

import ai_assistant
import files_manager
import files_ops as fo
import files_server as fs
import hub_apps
import hub_chat
from test_files_server import H, client, sign_in  # noqa: F401 - the fixture

PERSONAL = {"name": "anna-home", "path": "/mnt/alvaos/main/anna-home", "protocol": "smb",
            "smb_permissions": {"anna": "write"}, "personal_for": "anna"}


@pytest.fixture
def store(client, monkeypatch):  # noqa: F811 - the fixture
    """The helper's app data operations, kept in a dict: (folder, rel) -> text."""
    files = {}
    calls = []

    def run(args, timeout=600, user=None):
        calls.append((args, user))
        op, root, rel = args[0], args[1], args[2] if len(args) > 2 else ""
        if op == "files-data-read":
            return ({"found": True, "text": files[(root, rel)]} if (root, rel) in files else {"found": False}), ""
        if op == "files-data-delete":
            files.pop((root, rel), None)
        return {}, ""

    def pipe(args, stream, chunk=0, user=None):
        calls.append((args, user))
        files[(args[1], args[2])] = stream.read().decode()
        return {"written": 1}, ""

    monkeypatch.setattr(files_manager, "run_helper", run)
    monkeypatch.setattr(files_manager, "pipe_helper", pipe)
    shares = json.loads(open(fs.SHARES_FILE).read())
    shares["4"] = PERSONAL
    open(fs.SHARES_FILE, "w").write(json.dumps(shares))
    client.files, client.data_calls = files, calls
    return client


def test_calendar_keeps_events_in_the_personal_folder_as_the_person(store):
    sign_in(store, "anna", "anna-pass")
    got = store.get("/api/calendar").get_json()
    assert [(p["id"], p["calendars"][0]["name"]) for p in got["places"]] == [("own", "anna")]
    event = {"title": "Dentist", "start": "2026-10-05T09:00", "end": "2026-10-05T10:00", "calendar": "main"}
    saved = store.post("/api/calendar/item", json={"place": "own", "kind": "event", "item": event}, headers=H)
    assert saved.status_code == 200
    item = saved.get_json()["item"]
    assert item["title"] == "Dentist" and item["id"]
    assert (["files-data-write", "/mnt/alvaos/main/anna-home", ".alvaos/calendar/calendar.json"], "anna") \
        in store.data_calls
    assert store.get("/api/calendar").get_json()["places"][0]["events"][0]["title"] == "Dentist"
    bad = {**event, "end": "2026-10-05T08:00"}
    assert store.post("/api/calendar/item", json={"place": "own", "kind": "event", "item": bad},
                      headers=H).status_code == 400
    task = store.post("/api/calendar/item", json={"place": "own", "kind": "task",
                                                  "item": {"title": "Call grandma", "date": "2026-10-06"}}, headers=H)
    assert task.status_code == 200
    store.post("/api/calendar/delete", json={"place": "own", "kind": "event", "id": item["id"]}, headers=H)
    data = store.get("/api/calendar").get_json()["places"][0]
    assert data["events"] == [] and data["tasks"][0]["title"] == "Call grandma"


def test_family_calendars_are_shared_folders_and_read_only_ones_stay_so(store):
    hub_apps.save({"apps": {"calendar": {"libraries": ["Family"]}}}, ["anna", "ben"], shares=["Family"])
    sign_in(store, "ben", "ben-pass")                         # Family: read only, no personal folder
    got = store.get("/api/calendar").get_json()
    assert got["has_own"] is False
    assert [(p["id"], p["writable"]) for p in got["places"]] == [("shared:Family", False)]
    refused = store.post("/api/calendar/item", json={"place": "shared:Family", "kind": "event", "item": {
        "title": "x", "start": "2026-10-05", "end": "2026-10-05", "all_day": True}}, headers=H)
    assert refused.status_code == 403
    sign_in(store, "anna", "anna-pass")
    ok = store.post("/api/calendar/item", json={"place": "shared:Family", "kind": "event", "item": {
        "title": "Holidays", "start": "2026-10-05", "end": "2026-10-09", "all_day": True}}, headers=H)
    assert ok.status_code == 200
    assert ("/mnt/alvaos/main/Family", ".alvaos/calendar/calendar.json") in store.files


def test_calendar_is_closed_when_it_is_off_for_someone(store):
    hub_apps.save({"apps": {"calendar": {"people": ["anna"]}}}, ["anna", "ben"])
    sign_in(store, "ben", "ben-pass")
    refused = store.get("/api/calendar")
    assert refused.status_code == 403 and "Calendar is not turned on" in refused.get_json()["error"]


class Stream:
    status_code = 200

    def __init__(self, lines):
        self.lines = lines

    def iter_lines(self, decode_unicode=True):
        return iter(self.lines)

    def close(self):
        pass


def sse(**delta):
    return "data: " + json.dumps({"choices": [{"delta": delta}]})


def test_chat_streams_the_answer_and_keeps_the_chat(store, monkeypatch, tmp_path):
    monkeypatch.setattr(ai_assistant, "SETTINGS_FILE", str(tmp_path / "ai.json"))
    sign_in(store, "anna", "anna-pass")
    assert store.get("/api/chat").status_code == 403                    # off until the admin turns it on
    hub_apps.save({"apps": {"chat": {"enabled": True}}}, ["anna", "ben"])
    assert "No AI service" in store.get("/api/chat").get_json()["problem"]
    (tmp_path / "ai.json").write_text(json.dumps({"provider": "openai", "api_key": "sk-x", "model": "gpt-4o-mini"}))
    sent = []

    def post(url, headers=None, json=None, stream=False, timeout=None):
        sent.append(json)
        return Stream([sse(reasoning="Hmm"), sse(content="Hel"), sse(content="lo!"), "data: [DONE]"])

    monkeypatch.setattr("requests.post", post)
    assert store.get("/api/chat").get_json()["models"] == ["gpt-4o-mini"]
    res = store.post("/api/chat/send", json={"message": "Hi there", "reasoning": True, "effort": "high"}, headers=H)
    lines = [json.loads(line) for line in res.data.decode().splitlines()]
    assert lines[0]["chat"]["title"] == "Hi there"
    assert "".join(x.get("t", "") for x in lines) == "Hello!" and lines[1] == {"r": "Hmm"}
    assert lines[-1]["done"] is True
    assert sent[0]["reasoning_effort"] == "high" and sent[0]["stream"] is True and "tools" not in sent[0]
    chat_id = lines[0]["chat"]["id"]
    chat = store.get(f"/api/chat/{chat_id}").get_json()
    assert [(m["role"], m["content"]) for m in chat["messages"]] == [("user", "Hi there"), ("assistant", "Hello!")]
    assert chat["messages"][1]["reasoning"] == "Hmm"
    assert store.get("/api/chat").get_json()["chats"][0]["id"] == chat_id
    # Only the models the admin chose
    assert store.post("/api/chat/send", json={"message": "x", "model": "gpt-5"}, headers=H).status_code == 400
    store.post(f"/api/chat/{chat_id}/rename", json={"title": "Greeting"}, headers=H)
    assert store.get("/api/chat").get_json()["chats"][0]["title"] == "Greeting"
    store.post(f"/api/chat/{chat_id}/delete", headers=H)
    assert store.get("/api/chat").get_json()["chats"] == []
    assert store.get(f"/api/chat/{chat_id}").status_code == 404


def test_chat_tries_again_without_thinking_when_the_model_cannot(monkeypatch):
    class Refused:
        status_code = 400
        text = ""

        def json(self):
            return {"error": {"message": '"llama3.1" does not support thinking'}}

    sent = []

    def post(url, headers=None, json=None, stream=False, timeout=None):
        sent.append(dict(json))
        return Refused() if "reasoning_effort" in json else Stream([sse(content="<think>a</thi"),
                                                                    sse(content="nk>\nAnswer")])

    service = {"provider": "ollama-local", "base_url": "http://x:11434/v1", "api_key": "", "model": "llama3.1"}
    out = list(hub_chat.stream_answer(service, "llama3.1", [{"role": "user", "content": "q"}], True, "low", post))
    assert out[0][0] == "note" and len(sent) == 2
    assert "".join(t for k, t in out if k == "r") == "a" and "".join(t for k, t in out if k == "t") == "Answer"


def test_the_admin_chooses_the_chat_models(tmp_path):
    path = str(tmp_path / "hub.json")
    settings, _ = hub_apps.save({"apps": {"chat": {"models": ["llama3.1", "qwen3:8b"]}}}, [], path)
    assert settings["apps"]["chat"]["models"] == ["llama3.1", "qwen3:8b"]
    assert hub_apps.save({"apps": {"chat": {"models": ["bad name!"]}}}, [], path)[0] is None
    assert hub_apps.save({"apps": {"photos": {"models": ["x"]}}}, [], path)[0] is None


def test_app_data_files_are_replaced_whole_and_stay_inside(tmp_path):
    root = tmp_path / "mnt"
    share = root / "main" / "anna-home"
    share.mkdir(parents=True)
    assert fo.read_app_data(str(share), ".alvaos/chat/chats.json", str(root)) == {"found": False}
    fo.write_app_data(str(share), ".alvaos/chat/chats.json", io.BytesIO(b'{"a":1}'), str(root))
    fo.write_app_data(str(share), ".alvaos/chat/chats.json", io.BytesIO(b'{"a":2}'), str(root))
    assert fo.read_app_data(str(share), ".alvaos/chat/chats.json", str(root)) == {"found": True, "text": '{"a":2}'}
    assert sorted(p.name for p in (share / ".alvaos" / "chat").iterdir()) == ["chats.json"]
    for bad in ("../x.json", ".alvaos/../../x", "a/b/c/d/e.json", ".alvaos", "x/.hidden"):
        with pytest.raises(fo.FileOpError):
            fo.write_app_data(str(share), bad, io.BytesIO(b"x"), str(root))
    fo.delete_app_data(str(share), ".alvaos/chat/chats.json", str(root))
    fo.delete_app_data(str(share), ".alvaos/chat/chats.json", str(root))
    assert fo.read_app_data(str(share), ".alvaos/chat/chats.json", str(root)) == {"found": False}

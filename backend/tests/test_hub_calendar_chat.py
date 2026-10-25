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
    # A file from before there were several services: one service "main".
    assert [m["id"] for m in store.get("/api/chat").get_json()["models"]] == ["main/gpt-4o-mini"]
    ai_assistant.save_settings(ai_assistant.load_settings(), {"connections": [
        {"id": "main", "provider": "openai", "models": ["gpt-4o-mini"], "thinking": {"gpt-4o-mini": "effort"}}]})
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
    assert store.post("/api/chat/send", json={"message": "x", "model": "main/gpt-5"}, headers=H).status_code == 400
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
    # Before there were several services, a list of names of the one service.
    (tmp_path / "hub.json").write_text(json.dumps({"apps": {"chat": {"models": ["llama3.1", "qwen3:8b"]}}}))
    assert hub_apps.load(path)["apps"]["chat"]["access"] == {"mode": "only", "items": ["main/llama3.1", "main/qwen3:8b"]}
    settings, _ = hub_apps.save({"apps": {"chat": {"access": {"mode": "except", "items": ["cloud"]},
                                                   "people_access": {"ben": {"mode": "only", "items": ["home/llama3.1"]}},
                                                   "keep_days": 7}}}, ["anna", "ben"], path)
    chat = settings["apps"]["chat"]
    assert chat["access"]["mode"] == "except" and chat["people_access"]["ben"]["items"] == ["home/llama3.1"]
    assert chat["keep_days"] == 7 and hub_apps.load(path)["apps"]["chat"]["keep_days"] == 7
    assert hub_apps.save({"apps": {"chat": {"people_access": {"ben": None}}}}, ["ben"], path)[0]["apps"]["chat"]["people_access"] == {}
    assert hub_apps.save({"apps": {"chat": {"access": {"mode": "only", "items": ["bad name!"]}}}}, [], path)[0] is None
    assert hub_apps.save({"apps": {"chat": {"people_access": {"eve": None}}}}, ["ben"], path)[0] is None
    assert hub_apps.save({"apps": {"chat": {"keep_days": -1}}}, [], path)[0] is None
    assert hub_apps.save({"apps": {"photos": {"access": {"mode": "all"}}}}, [], path)[0] is None


def two_services():
    ai_assistant.save_settings(ai_assistant.load_settings(), {"connections": [
        {"provider": "ollama-local", "name": "Home", "base_url": "http://192.168.1.5:11434/v1",
         "models": ["llama3.1", "qwen3:8b"]},
        {"provider": "openai", "name": "Cloud", "api_key": "sk-1", "models": ["gpt-5", "gpt-4o-mini"]},
        {"provider": "openai", "name": "No key", "models": ["gpt-4o"]}]})


def test_several_services_with_several_models_and_rules_for_people():
    two_services()
    ids = [m["id"] for m in hub_chat.catalog()]
    assert ids == ["home/llama3.1", "home/qwen3:8b", "cloud/gpt-5", "cloud/gpt-4o-mini"]   # without a key: not offered
    thinking = {m["id"]: m["thinking"] for m in hub_chat.catalog()}
    assert thinking == {"home/llama3.1": "none", "home/qwen3:8b": "on", "cloud/gpt-5": "effort", "cloud/gpt-4o-mini": "none"}
    hub_apps.save({"apps": {"chat": {"enabled": True, "access": {"mode": "except", "items": ["cloud/gpt-5"]},
                                     "people_access": {"ben": {"mode": "only", "items": ["home"]}}}}}, ["anna", "ben"])
    assert [m["id"] for m in hub_chat.offered("anna")[0]] == ["home/llama3.1", "home/qwen3:8b", "cloud/gpt-4o-mini"]
    assert [m["id"] for m in hub_chat.offered("ben")[0]] == ["home/llama3.1", "home/qwen3:8b"]
    hub_apps.save({"apps": {"chat": {"people_access": {"ben": {"mode": "only", "items": ["cloud/gpt-5"]}}}}}, ["ben"])
    assert hub_chat.offered("ben") == ([], "No model is open to you. Ask the person who looks after the NAS.")
    # Keys never reach the page.
    public = json.dumps(ai_assistant.public_settings(ai_assistant.load_settings()))
    assert "sk-1" not in public and '"key_set": true' in public


def test_a_service_keeps_its_key_and_gets_a_name_of_its_own():
    two_services()
    s = ai_assistant.load_settings()
    sent = [{**{k: v for k, v in c.items() if k != "api_key"}, "api_key": ""} for c in s["connections"]]
    sent.append({"provider": "openai", "name": "Cloud", "api_key": "sk-2", "models": []})
    s, problem = ai_assistant.save_settings(s, {"connections": sent})
    assert problem == ""
    assert [c["id"] for c in s["connections"]] == ["home", "cloud", "no-key", "cloud-2"]
    assert s["connections"][1]["api_key"] == "sk-1" and s["connections"][3]["api_key"] == "sk-2"
    assert ai_assistant.save_settings(s, {"connections": [{"provider": "other", "base_url": "ftp://x"}]})[1]
    assert ai_assistant.save_settings(s, {"connections": [{"provider": "openai", "models": ["a b"]}]})[1]
    s, problem = ai_assistant.save_settings(s, {"assistant": {"connection": "cloud", "model": "gpt-5"}, "enabled": True})
    assert problem == "" and (s["model"], s["base_url"], s["api_key"]) == ("gpt-5", "https://api.openai.com/v1", "sk-1")
    assert ai_assistant.save_settings(s, {"assistant": {"connection": "cloud", "model": "nope"}})[1]


def test_the_models_of_a_service_are_read_from_it():
    class Res:
        status_code = 200

        def json(self):
            return {"data": [{"id": "llama3.1"}, {"id": "qwen3:8b"}, {"id": "bad name"}]}
    seen = []
    found, problem = ai_assistant.list_models({"base_url": "http://x:11434/v1", "api_key": "k"},
                                              lambda url, headers=None, timeout=None: seen.append((url, headers)) or Res())
    assert found == ["llama3.1", "qwen3:8b"] and problem == ""
    assert seen == [("http://x:11434/v1/models", {"Authorization": "Bearer k"})]


def chat_ready(store, monkeypatch, answer="Hi!"):
    sign_in(store, "anna", "anna-pass")
    two_services()
    hub_apps.save({"apps": {"chat": {"enabled": True}}}, ["anna", "ben"])
    sent = []

    def post(url, headers=None, json=None, stream=False, timeout=None):
        sent.append((url, json))
        return Stream([sse(content=answer), "data: [DONE]"])
    monkeypatch.setattr("requests.post", post)
    return sent


def say(store, **body):
    res = store.post("/api/chat/send", json=body, headers=H)
    return [json.loads(line) for line in res.data.decode().splitlines()]


def test_thinking_is_only_asked_for_where_the_model_can(store, monkeypatch):
    sent = chat_ready(store, monkeypatch)
    say(store, message="q", model="home/llama3.1", reasoning=True, effort="high")
    say(store, message="q", model="home/qwen3:8b", reasoning=True, effort="high")
    say(store, message="q", model="cloud/gpt-5", reasoning=True, effort="high")
    assert sent[0][0].startswith("http://192.168.1.5:11434/v1") and sent[0][1]["reasoning_effort"] == "none"
    assert sent[1][1]["reasoning_effort"] == "medium"     # on, but not how hard
    assert sent[2][1]["reasoning_effort"] == "high" and sent[2][0].startswith("https://api.openai.com")


def test_a_private_chat_is_kept_nowhere(store, monkeypatch):
    sent = chat_ready(store, monkeypatch)
    before = dict(store.files)
    lines = say(store, message="And now?", model="home/llama3.1", private=True,
                history=[{"role": "user", "content": "Secret"}, {"role": "assistant", "content": "Ok"}])
    assert lines[0]["chat"] == {"id": "", "title": "And now?", "private": True} and lines[-1]["done"]
    assert [m["content"] for m in sent[0][1]["messages"][1:]] == ["Secret", "Ok", "And now?"]
    assert store.files == before and store.get("/api/chat").get_json()["chats"] == []


def test_chats_go_after_30_days_unless_pinned_or_kept_and_the_flame_takes_the_rest(store, monkeypatch):
    chat_ready(store, monkeypatch)
    ids = [say(store, message=f"Chat {n}", model="home/llama3.1")[0]["chat"]["id"] for n in range(4)]
    store.post(f"/api/chat/{ids[0]}/pin", json={"pinned": True}, headers=H)
    store.post(f"/api/chat/{ids[1]}/keep", json={"kept": True}, headers=H)
    rows = {c["id"]: c for c in store.get("/api/chat").get_json()["chats"]}
    assert rows[ids[0]]["pinned"] and rows[ids[1]]["kept"] and store.get("/api/chat").get_json()["keep_days"] == 30
    # 31 days later the two others are gone.
    later = hub_chat.datetime.now(hub_chat.timezone.utc) + hub_chat.timedelta(days=31)

    class Later(hub_chat.datetime):
        @classmethod
        def now(cls, tz=None):
            return later
    monkeypatch.setattr(hub_chat, "datetime", Later)
    assert sorted(c["id"] for c in store.get("/api/chat").get_json()["chats"]) == sorted(ids[:2])
    assert store.get(f"/api/chat/{ids[2]}").status_code == 404
    monkeypatch.setattr(hub_chat, "datetime", hub_chat.datetime.__mro__[1])
    new = say(store, message="Fresh", model="home/llama3.1")[0]["chat"]["id"]
    burnt = store.post("/api/chat/burn", json={}, headers=H).get_json()
    assert burnt == {"success": True, "deleted": 1, "stayed": 2} and store.get(f"/api/chat/{new}").status_code == 404
    assert store.post("/api/chat/burn", json={"everything": True}, headers=H).get_json()["deleted"] == 2
    assert store.get("/api/chat").get_json()["chats"] == []


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

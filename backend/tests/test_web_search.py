"""Web search in Chat through DuckDuckGo (web_search.py, hub_chat.py)."""

import json

import ai_assistant
import hub_apps
import hub_chat
import web_search
from test_files_server import H, client, sign_in  # noqa: F401 - the fixture
from test_hub_calendar_chat import chat_ready, say, store  # noqa: F401 - the fixtures

HTML_PAGE = """
<div class="result results_links results_links_deep result--ad">
  <h2 class="result__title"><a rel="nofollow" class="result__a" href="https://duckduckgo.com/y.js?ad_domain=shop.example">Buy a NAS now</a></h2>
  <a class="result__snippet" href="https://duckduckgo.com/y.js?x">An ad</a>
</div>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title"><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2FSnowTimSwiss%2FAlvaOS&amp;rut=abc">Snow<b>Tim</b>Swiss/AlvaOS</a></h2>
  <a class="result__url" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com">github.com</a>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">A calm <b>NAS</b> OS for storage,
     apps &amp; backup.</a>
</div>
<div class="result results_links web-result">
  <h2 class="result__title"><a rel="nofollow" class="result__a" href="https://example.org/nas">What is a NAS?</a></h2>
  <a class="result__snippet" href="https://example.org/nas">Network attached storage.</a>
</div>
"""

LITE_PAGE = """
<table>
<tr><td valign="top">1.&nbsp;</td><td><a rel="nofollow" href="https://example.org/a" class='result-link'>First</a></td></tr>
<tr><td>&nbsp;</td><td class='result-snippet'>The <b>first</b> one.</td></tr>
<tr><td valign="top">2.&nbsp;</td><td><a rel="nofollow" href="https://example.org/b" class='result-link'>Second</a></td></tr>
</table>
"""


class Res:
    def __init__(self, text="", status=200, headers=None, body=b""):
        self.text, self.status_code, self.headers, self.body = text, status, headers or {}, body
        self.encoding = "utf-8"

    def iter_content(self, size):
        yield self.body

    def close(self):
        pass


def test_results_are_read_from_duckduckgo_without_ads():
    found = web_search.parse_results(HTML_PAGE)
    assert [(r["title"], r["url"]) for r in found] == [
        ("SnowTimSwiss/AlvaOS", "https://github.com/SnowTimSwiss/AlvaOS"), ("What is a NAS?", "https://example.org/nas")]
    assert found[0]["snippet"] == "A calm NAS OS for storage, apps & backup."
    lite = web_search.parse_results(LITE_PAGE)
    assert [(r["title"], r["snippet"]) for r in lite] == [("First", "The first one."), ("Second", "")]


def test_the_lite_page_is_asked_when_the_first_has_nothing():
    asked = []

    def post(url, data=None, headers=None, timeout=None):
        asked.append((url, data["q"]))
        return Res("<html>nothing</html>") if "html." in url else Res(LITE_PAGE)
    found, problem = web_search.search("  what   is a NAS ", post)
    assert problem == "" and len(found) == 2
    assert asked == [("https://html.duckduckgo.com/html/", "what is a NAS"), ("https://lite.duckduckgo.com/lite/", "what is a NAS")]
    assert web_search.search("x", lambda *a, **k: Res(status=403)) == ([], "DuckDuckGo answered 403.")


def test_pages_on_the_home_network_are_never_read():
    assert web_search._public("http://127.0.0.1:8090/") is False
    assert web_search._public("http://192.168.1.20/admin") is False
    assert web_search._public("http://[::1]/") is False
    assert web_search._public("file:///etc/passwd") is False
    asked = []

    def get(url, **kw):
        asked.append(url)
        return Res(status=302, headers={"Location": "http://192.168.1.1/"})
    # A page on the internet that sends on to the router: stops there.
    out = web_search.read_page("https://example.org/", get, public=lambda u: "192.168" not in u)
    assert out == "" and asked == ["https://example.org/"]


def test_a_page_becomes_plain_text():
    page = b"<html><head><style>x{}</style><script>alert(1)</script></head><body><nav>Menu</nav>" \
           b"<h1>Title</h1><p>First   line.</p><p>Second &amp; last.</p></body></html>"
    text = web_search.read_page("https://example.org/", lambda url, **kw: Res(headers={"Content-Type": "text/html"}, body=page),
                                public=lambda u: True)
    assert text == "Title\nFirst line.\nSecond & last."


def test_chat_searches_the_web_when_asked_and_names_the_sources(store, monkeypatch):  # noqa: F811
    sent = chat_ready(store, monkeypatch, answer="AlvaOS is a NAS OS [1].")
    found = [{"title": "AlvaOS", "url": "https://github.com/SnowTimSwiss/AlvaOS", "snippet": "A calm NAS OS", "text": "AlvaOS is calm."}]
    queries = []
    monkeypatch.setattr(hub_chat.web_search, "gather", lambda q: queries.append(q) or (found, ""))
    # Off until the admin turns it on.
    assert store.get("/api/chat").get_json()["search"] is False
    say(store, message="What is AlvaOS?", model="home/llama3.1", search=True)
    assert queries == [] and len(sent[0][1]["messages"]) == 2
    ai_assistant.save_settings(ai_assistant.load_settings(), {"search": {"enabled": True}})
    assert store.get("/api/chat").get_json()["search"] is True
    lines = say(store, message="What is AlvaOS?", model="home/llama3.1", search=True)
    assert queries == ["What is AlvaOS?"]
    assert {"searching": "What is AlvaOS?"} in lines and {"sources": [{"title": "AlvaOS", "url": found[0]["url"]}]} in lines
    messages = sent[1][1]["messages"]
    assert messages[-2]["role"] == "system" and "[1] AlvaOS" in messages[-2]["content"] and "AlvaOS is calm." in messages[-2]["content"]
    assert messages[-1] == {"role": "user", "content": "What is AlvaOS?"}
    chat = store.get(f"/api/chat/{lines[0]['chat']['id']}").get_json()
    assert chat["messages"][-1]["sources"][0]["url"] == found[0]["url"]
    # Only for the people the admin chose.
    hub_apps.save({"apps": {"chat": {"search_people": ["ben"]}}}, ["anna", "ben"])
    assert store.get("/api/chat").get_json()["search"] is False
    say(store, message="Again?", model="home/llama3.1", search=True)
    assert len(queries) == 1


def test_nothing_found_still_answers(store, monkeypatch):  # noqa: F811
    chat_ready(store, monkeypatch)
    ai_assistant.save_settings(ai_assistant.load_settings(), {"search": {"enabled": True}})
    monkeypatch.setattr(hub_chat.web_search, "gather", lambda q: ([], "The NAS cannot reach DuckDuckGo."))
    lines = say(store, message="News?", model="home/llama3.1", search=True)
    assert {"note": "No web search this time: The NAS cannot reach DuckDuckGo."} in lines and lines[-1]["done"]


def test_a_follow_up_is_turned_into_a_query_by_the_model():
    history = [{"role": "user", "content": "Weather in Bern today?"}, {"role": "assistant", "content": "Sunny."}]

    class Answer:
        def json(self):
            return {"choices": [{"message": {"content": "<think>hm</think>\n\"Bern weather tomorrow\""}}]}
    asked = []
    service = {"provider": "openai", "base_url": "https://api.openai.com/v1", "api_key": "k"}
    q = hub_chat.search_query(service, "gpt-4o-mini", history, "And tomorrow?",
                              lambda url, headers=None, json=None, timeout=None: asked.append(json) or Answer())
    assert q == "Bern weather tomorrow" and "And tomorrow?" in asked[0]["messages"][-1]["content"]
    assert hub_chat.search_query(service, "m", [], "First question", lambda *a, **k: 1 / 0) == "First question"


def test_the_setting_is_kept(tmp_path):
    path = str(tmp_path / "ai.json")
    s, problem = ai_assistant.save_settings(ai_assistant.load_settings(path), {"search": {"enabled": True}}, path=path)
    assert problem == "" and ai_assistant.load_settings(path)["search"] == {"enabled": True}
    assert json.loads(open(path).read())["search"] == {"enabled": True}
    assert ai_assistant.public_settings(s)["search"] == {"enabled": True}


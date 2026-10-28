"""Office in the Hub (hub_office.py): tokens, the editor's settings, the Document Server's messages, new documents."""

import io
import time
import zipfile
import xml.etree.ElementTree as ET

import pytest

import hub_office as ho

SECRET = "s3cret"


def test_formats_say_what_opens_and_what_may_be_changed():
    assert ho.format_of("Budget.XLSX") == ("xlsx", "cell", True)
    assert ho.format_of("old.doc") == ("doc", "word", False)           # shown only
    assert ho.format_of("slides.pptx") == ("pptx", "slide", True)
    assert ho.format_of("photo.jpg") is None and ho.format_of("README") is None


def test_a_jwt_is_checked_for_its_signature_algorithm_and_age():
    token = ho.jwt_encode({"a": 1}, SECRET)
    assert ho.jwt_decode(token, SECRET) == {"a": 1}
    assert ho.jwt_decode(token, "other") is None
    head, body, sig = token.split(".")
    assert ho.jwt_decode(f"{head}.{body}x.{sig}", SECRET) is None       # changed content
    assert ho.jwt_decode("a.b", SECRET) is None and ho.jwt_decode("", SECRET) is None and ho.jwt_decode(token, "") is None
    none_alg = ho._b64(b'{"alg":"none","typ":"JWT"}') + "." + body + "."
    assert ho.jwt_decode(none_alg, SECRET) is None                       # no "none" signature
    old = ho.jwt_encode({"exp": time.time() - 3600}, SECRET)
    assert ho.jwt_decode(old, SECRET) is None


def test_file_tokens_are_for_one_file_and_one_person_and_run_out():
    now = 1_700_000_000
    token = ho.file_token(SECRET, "anna", "Family", "Docs/plan.docx", True, now)
    claims = ho.read_file_token(SECRET, token, now + 60)
    assert claims and claims["u"] == "anna" and claims["s"] == "Family" and claims["r"] == "Docs/plan.docx" and claims["edit"]
    assert ho.read_file_token(SECRET, token, now + ho.TOKEN_SECONDS + 3600) is None
    assert ho.read_file_token("other", token, now) is None
    assert ho.read_file_token(SECRET, ho.jwt_encode({"u": "anna", "exp": now + 99}, SECRET), now) is None   # not an office token


def test_the_editor_settings_follow_the_file_and_the_rights():
    config = ho.editor_config(name="plan.docx", share="Family", rel="Docs/plan.docx", mtime=1700000000, size=123, user="anna",
                              edit=True, file_url="http://host/f", callback_url="http://host/c")
    assert config["documentType"] == "word" and config["document"]["fileType"] == "docx"
    assert config["editorConfig"]["mode"] == "edit" and config["document"]["permissions"]["edit"] is True
    assert config["editorConfig"]["callbackUrl"] == "http://host/c" and config["editorConfig"]["user"]["id"] == "anna"
    key = config["document"]["key"]
    again = ho.editor_config(name="plan.docx", share="Family", rel="Docs/plan.docx", mtime=1700000001, size=123, user="ben",
                             edit=True, file_url="u", callback_url="c")
    assert again["document"]["key"] != key                                # the file changed: a new key
    viewing = ho.editor_config(name="plan.docx", share="Family", rel="Docs/plan.docx", mtime=1, size=1, user="ben",
                               edit=False, file_url="u", callback_url="c")
    assert viewing["editorConfig"]["mode"] == "view" and viewing["document"]["permissions"]["edit"] is False
    old = ho.editor_config(name="old.xls", share="S", rel="old.xls", mtime=1, size=1, user="a", edit=True, file_url="u", callback_url="c")
    assert old["editorConfig"]["mode"] == "view"                          # a format it only shows
    assert ho.editor_config(name="a.jpg", share="S", rel="a.jpg", mtime=1, size=1, user="a", edit=True, file_url="u", callback_url="c") is None
    signed = ho.sign_config(config, SECRET)
    assert ho.jwt_decode(signed["token"], SECRET)["document"]["key"] == key


def test_the_document_servers_message_must_be_signed_with_the_shared_secret():
    body = {"status": 2, "url": "http://ds/cache/files/x", "key": "k"}
    header = "Bearer " + ho.jwt_encode({"payload": body}, SECRET)
    assert ho.callback_claims(SECRET, header, body) == body
    assert ho.callback_claims(SECRET, "", {**body, "token": ho.jwt_encode(body, SECRET)}) == body     # signed in the body
    assert ho.callback_claims(SECRET, "Bearer " + ho.jwt_encode({"payload": body}, "other"), body) is None
    assert ho.callback_claims(SECRET, "", body) is None                    # nobody signed it
    assert ho.callback_claims(SECRET, "Basic abc", body) is None


@pytest.mark.parametrize("url,want", [
    ("http://nas:28082/cache/files/data/k_6/output.docx/output.docx?md5=a&expires=1&filename=output.docx",
     "/cache/files/data/k_6/output.docx/output.docx?md5=a&expires=1&filename=output.docx"),
    ("https://elsewhere.example/cache/files/x", "/cache/files/x"),       # the host is dropped: only the path is used
    ("http://ds/etc/passwd", None), ("http://ds/other", None),
    ("file:///cache/files/x", None), ("//ds/cache/x", None), ("", None),
])
def test_only_the_path_of_the_new_version_is_used_never_its_host(url, want):
    assert ho.ds_download_path(url) == want


@pytest.mark.parametrize("kind,main", [("docx", "word/document.xml"), ("xlsx", "xl/workbook.xml"), ("pptx", "ppt/presentation.xml")])
def test_new_documents_are_valid_office_packages(kind, main):
    data = ho.new_document(kind)
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None and main in z.namelist() and "[Content_Types].xml" in z.namelist()
    for name in z.namelist():
        ET.fromstring(z.read(name))                                      # every part is well-formed XML
    # Every part that is named in the content types or the relationships is in the package.
    types = ET.fromstring(z.read("[Content_Types].xml"))
    for el in types:
        if el.tag.endswith("Override"):
            assert el.attrib["PartName"].lstrip("/") in z.namelist(), el.attrib
    # And every relationship leads to a part that exists.
    import posixpath
    for name in z.namelist():
        if name.endswith(".rels"):
            base = posixpath.dirname(posixpath.dirname(name))
            for rel in ET.fromstring(z.read(name)):
                target = posixpath.normpath(posixpath.join(base, rel.attrib["Target"]))
                assert target in z.namelist(), (name, target)
    assert ho.new_document("exe") is None


class FakeStore:
    def __init__(self):
        self.apps = {}
        self.status = {"status": "idle"}
        self.installs = []

    def _load_apps_state(self):
        return self.apps

    def get_install_status(self):
        return self.status

    def install_app(self, **kw):
        self.installs.append(kw)
        self.status = {"app_id": kw["app_id"], "status": "installing", "progress": 40, "message": "Pulling the image"}
        return True, None


def test_turning_office_on_installs_the_editor_once_and_the_page_follows_it(monkeypatch):
    import office_admin
    store = FakeStore()
    assert office_admin.state(store, False)["state"] == "off"
    assert office_admin.state(store, True)["state"] == "missing"
    assert office_admin.ensure_installed(store, "/mnt/alvaos/main") == (True, "")
    (call,) = store.installs
    assert call["app_id"] == ho.DS_APP_ID and call["pool_path"] == "/mnt/alvaos/main"
    assert len(call["environment_vars"]["JWT_SECRET"]) >= 32 and call["port_mappings"] is None
    assert office_admin.ensure_installed(store, "/mnt/alvaos/main") == (True, "") and len(store.installs) == 1   # on its way
    assert office_admin.state(store, True) == {"state": "installing", "message": "Pulling the image", "progress": 40}
    store.apps[ho.DS_APP_ID] = {}
    store.status = {"status": "idle"}
    assert office_admin.state(store, True, ready=False)["state"] == "starting"
    assert office_admin.state(store, True, ready=True)["state"] == "ready"
    assert office_admin.ensure_installed(store, "/x") == (True, "") and len(store.installs) == 1               # there already
    broken = FakeStore()
    broken.status = {"app_id": ho.DS_APP_ID, "status": "error", "message": "No space left"}
    assert office_admin.state(broken, True) == {"state": "error", "message": "No space left", "progress": 0}


def test_the_editor_is_reached_only_through_the_hubs_proxy_when_office_is_on(tmp_path):
    import hub_apps
    settings, _ = hub_apps.save({"apps": {"office": {"enabled": True}}}, ["anna"], path=str(tmp_path / "hub.json"))
    assert hub_apps.office_port(settings, {}) is None                        # not installed yet
    assert hub_apps.office_port(settings, {ho.DS_APP_ID: {}}) == ho.DS_PORT
    import hub_apps as h
    h.STORE_STATE_FILE = str(tmp_path / "state.json")
    (tmp_path / "state.json").write_text('{"hub-office": {}}')
    assert hub_apps.embed_targets(settings, []) == {ho.DS_PORT + 20000: ho.DS_PORT}
    off, _ = hub_apps.save({"apps": {"office": {"enabled": False}}}, ["anna"], path=str(tmp_path / "h2.json"))
    assert hub_apps.office_port(off, {ho.DS_APP_ID: {}}) is None


def test_the_editor_counts_as_reachable_only_when_the_proxy_answers_too(monkeypatch):
    asked = []

    def answers(port=ho.DS_PORT, timeout=2.0):
        asked.append(port)
        return port == ho.DS_PORT

    monkeypatch.setattr(ho, "editor_answers", answers)
    assert ho.editor_reachable() is False       # the editor is up, its proxy is not
    assert asked == [ho.DS_PORT, ho.DS_PORT + 20000]
    monkeypatch.setattr(ho, "editor_answers", lambda port=0, timeout=2.0: True)
    assert ho.editor_reachable() is True


def test_a_token_whose_header_is_not_an_object_is_refused():
    # Any caller can send such a token; it must be refused, not crash the request.
    import hub_office
    token = '.'.join([hub_office._b64(b'[]'), hub_office._b64(b'{}'), 'c2ln'])
    assert hub_office.jwt_decode(token, 'secret') is None

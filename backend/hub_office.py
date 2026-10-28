"""Office in the Hub: documents, spreadsheets and presentations edited in the browser.

The editor is EuroOffice's Document Server (a fork of ONLYOFFICE's), a Docker app that AlvaOS
installs and starts by itself when the admin turns Office on (the catalog entry `hub-office`,
not shown in the App Store). It listens on the NAS's own address only (127.0.0.1:8082); people
reach it through the Hub's proxy for apps (embed_proxy.py, port + 20000), which is why it works
at home, away from home through AlvaOS Link and in the computer and phone apps.

How a document is opened and saved:

1. The page asks the Hub (`POST /api/office/config`) to open a file. The Hub checks the person's
   rights on the share and answers with the editor's settings: a link to the file and a link to
   save to, both with a token of their own (not the person's session: the Document Server is no
   person), and the whole thing signed with the Document Server's secret (JWT).
2. The page loads the editor's script from the Document Server and starts it with those settings.
3. The Document Server fetches the file from the Hub (`GET /api/office/file`) and, while people
   edit, tells the Hub when it has a new version (`POST /api/office/callback`). The Hub fetches that
   version from the Document Server (only from it, whatever address the message names) and saves it
   over the file *as the person* (the privilege helper's `files-replace`), so Linux checks the rights.

Nothing of this is a second copy of the files: the document stays a normal file in the share.
"""

import base64
import hashlib
import hmac
import io
import json
import re
import time
import urllib.request
import zipfile
from typing import Any, Dict, Optional, Tuple

DS_APP_ID = 'hub-office'
DS_PORT = 8082
TOKEN_SECONDS = 12 * 3600       # an editing session may last a working day
JWT_FUTURE = 60

# extension -> (document type of the editor, whether the editor can change it). The old binary formats
# (doc, xls, ppt) and PDF are shown only; the Document Server edits the rest.
FORMATS: Dict[str, Tuple[str, bool]] = {
    'docx': ('word', True), 'odt': ('word', True), 'rtf': ('word', True), 'txt': ('word', True),
    'doc': ('word', False), 'pdf': ('word', False),
    'xlsx': ('cell', True), 'ods': ('cell', True), 'csv': ('cell', True), 'xls': ('cell', False),
    'pptx': ('slide', True), 'odp': ('slide', True), 'ppt': ('slide', False),
}
NEW_KINDS = {'docx': 'Document', 'xlsx': 'Spreadsheet', 'pptx': 'Presentation'}


def format_of(name: str) -> Optional[Tuple[str, str, bool]]:
    """(extension, document type, may be edited) for a file name the editor opens, else None."""
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    found = FORMATS.get(ext)
    return (ext, found[0], found[1]) if found else None


# ── JWT (HS256), what the Document Server and the Hub sign with ───────────────────────────────

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def jwt_encode(payload: Dict[str, Any], secret: str) -> str:
    head = _b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}, separators=(',', ':')).encode())
    body = _b64(json.dumps(payload, separators=(',', ':')).encode())
    sig = hmac.new(secret.encode(), f'{head}.{body}'.encode(), hashlib.sha256).digest()
    return f'{head}.{body}.{_b64(sig)}'


def jwt_decode(token: str, secret: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The payload of a token signed with `secret` and not expired, else None."""
    parts = str(token or '').split('.')
    if len(parts) != 3 or not secret:
        return None
    try:
        head = json.loads(_unb64(parts[0]))
        if head.get('alg') != 'HS256':
            return None
        want = hmac.new(secret.encode(), f'{parts[0]}.{parts[1]}'.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _unb64(parts[2])):
            return None
        payload = json.loads(_unb64(parts[1]))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get('exp')
    if isinstance(exp, (int, float)) and exp < (now if now is not None else time.time()) - JWT_FUTURE:
        return None
    return payload


# ── The Hub's own tokens for the file link and the save link ───────────────────────────────────

def file_token(secret: str, user: str, share: str, rel: str, edit: bool, now: Optional[float] = None,
               role: str = 'user') -> str:
    """A token for one file, for the Document Server to fetch it and save it again."""
    return jwt_encode({'for': 'office', 'u': user, 'role': role, 's': share, 'r': rel, 'edit': bool(edit),
                       'exp': (now if now is not None else time.time()) + TOKEN_SECONDS}, secret)


def read_file_token(secret: str, token: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    claims = jwt_decode(token, secret, now)
    return claims if claims and claims.get('for') == 'office' and isinstance(claims.get('u'), str) else None


def editor_answers(port: int = DS_PORT, timeout: float = 2.0) -> bool:
    """Is the Document Server up (its health check says "true")?"""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthcheck', timeout=timeout) as res:
            return bool(res.status == 200 and res.read(16).strip() == b'true')
    except (OSError, ValueError):
        return False


def editor_reachable() -> bool:
    """Is the editor up *and* does the Hub's proxy for it carry the same answer (what people's browsers use)?"""
    import embed_proxy
    through = embed_proxy.embed_port(DS_PORT)
    return editor_answers() and bool(through) and editor_answers(through)


# ── The editor's settings ───────────────────────────────────────────────────────────────────────

def doc_key(share: str, rel: str, mtime: float, size: int) -> str:
    """The Document Server's name for this version of the file: it changes when the file does, so a
    document changed on the NAS in between is not shown from the editor's old cache."""
    digest = hashlib.sha256(f'{share}\0{rel}\0{int(mtime)}\0{size}'.encode()).hexdigest()
    return digest[:40]


def editor_config(*, name: str, share: str, rel: str, mtime: float, size: int, user: str, edit: bool,
                  file_url: str, callback_url: str, phone: bool = False, lang: str = 'en') -> Optional[Dict[str, Any]]:
    """The settings the page gives the editor (without the signature), or None for a file it cannot open."""
    found = format_of(name)
    if not found:
        return None
    ext, kind, editable = found
    allow = bool(edit and editable)
    return {
        'documentType': kind,
        'type': 'mobile' if phone else 'desktop',
        'width': '100%', 'height': '100%',
        'document': {
            'fileType': ext, 'key': doc_key(share, rel, mtime, size), 'title': name, 'url': file_url,
            'permissions': {'edit': allow, 'download': True, 'print': True, 'copy': True, 'comment': allow},
        },
        'editorConfig': {
            'mode': 'edit' if allow else 'view', 'lang': lang, 'callbackUrl': callback_url,
            'user': {'id': user, 'name': user},
            'customization': {'autosave': True, 'forcesave': True, 'compactHeader': True, 'hideRightMenu': True,
                              'chat': False, 'help': False, 'feedback': False},
        },
    }


def sign_config(config: Dict[str, Any], secret: str) -> Dict[str, Any]:
    """The settings with the Document Server's signature (it refuses unsigned ones)."""
    return {**config, 'token': jwt_encode(config, secret)}


def callback_claims(secret: str, header: str, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What the Document Server's message says, if it is signed with the shared secret: the signature is
    in the header ("Bearer ..." with the message under `payload`) or in the message's own `token`."""
    bearer = header[7:] if str(header).lower().startswith('bearer ') else ''
    if bearer:
        claims = jwt_decode(bearer, secret)
        if claims is not None:
            inner = claims.get('payload')
            return inner if isinstance(inner, dict) else claims
    token = body.get('token')
    if isinstance(token, str):
        claims = jwt_decode(token, secret)
        if claims is not None:
            return claims
    return None


# Status values of the callback that carry a saved version: 2 (closed, saved) and 6 (saved while editing).
SAVED_STATUSES = (2, 6)
DS_PATH_RE = re.compile(r'^/[A-Za-z0-9._~%!$&\'()*+,;=:@/-]*(\?[A-Za-z0-9._~%!$&\'()*+,;=:@/?-]*)?$')


def ds_download_path(url: str) -> Optional[str]:
    """The path and query of the address the Document Server names for the new version. Only these are
    used (the Hub fetches them from the Document Server itself): whatever host the message names, the
    Hub never goes there."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    path = parts.path + (f'?{parts.query}' if parts.query else '')
    return path if parts.scheme in ('http', 'https') and path.startswith('/cache/') and DS_PATH_RE.match(path) else None


# ── Empty documents for "New document / spreadsheet / presentation" ─────────────────────────────

XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
NS_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS_CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
NS_OD = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def _zip(files: Dict[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(zipfile.ZipInfo(name, (2024, 1, 1, 0, 0, 0)), XML + text if text.startswith('<') else text)
    return out.getvalue()


def _rels(items: Dict[str, Tuple[str, str]]) -> str:
    inner = ''.join(f'<Relationship Id="{i}" Type="{t}" Target="{target}"/>' for i, (t, target) in items.items())
    return f'<Relationships xmlns="{NS_REL}">{inner}</Relationships>'


def _content_types(defaults: Dict[str, str], overrides: Dict[str, str]) -> str:
    d = ''.join(f'<Default Extension="{e}" ContentType="{c}"/>' for e, c in defaults.items())
    o = ''.join(f'<Override PartName="{p}" ContentType="{c}"/>' for p, c in overrides.items())
    return f'<Types xmlns="{NS_CT}">{d}{o}</Types>'


RT = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
BASE_DEFAULTS = {'rels': 'application/vnd.openxmlformats-package.relationships+xml', 'xml': 'application/xml'}


def new_document(kind: str) -> Optional[bytes]:
    """A new, empty .docx, .xlsx or .pptx that every office program opens."""
    if kind == 'docx':
        return _zip({
            '[Content_Types].xml': _content_types(BASE_DEFAULTS, {
                '/word/document.xml': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'}),
            '_rels/.rels': _rels({'rId1': (RT + 'officeDocument', 'word/document.xml')}),
            'word/document.xml': '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                 '<w:body><w:p/><w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                                 '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" '
                                 'w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>',
        })
    if kind == 'xlsx':
        return _zip({
            '[Content_Types].xml': _content_types(BASE_DEFAULTS, {
                '/xl/workbook.xml': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml',
                '/xl/worksheets/sheet1.xml': 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'}),
            '_rels/.rels': _rels({'rId1': (RT + 'officeDocument', 'xl/workbook.xml')}),
            'xl/workbook.xml': f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                               f'xmlns:r="{NS_OD}"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
            'xl/_rels/workbook.xml.rels': _rels({'rId1': (RT + 'worksheet', 'worksheets/sheet1.xml')}),
            'xl/worksheets/sheet1.xml': '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                                        '<sheetData/></worksheet>',
        })
    if kind == 'pptx':
        a = 'http://schemas.openxmlformats.org/drawingml/2006/main'
        p = 'http://schemas.openxmlformats.org/presentationml/2006/main'
        ct = 'application/vnd.openxmlformats-officedocument.'
        tree = ('<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                f'<p:grpSpPr><a:xfrm xmlns:a="{a}"><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
                '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>')
        return _zip({
            '[Content_Types].xml': _content_types(BASE_DEFAULTS, {
                '/ppt/presentation.xml': ct + 'presentationml.presentation.main+xml',
                '/ppt/slideMasters/slideMaster1.xml': ct + 'presentationml.slideMaster+xml',
                '/ppt/slideLayouts/slideLayout1.xml': ct + 'presentationml.slideLayout+xml',
                '/ppt/slides/slide1.xml': ct + 'presentationml.slide+xml',
                '/ppt/theme/theme1.xml': ct + 'theme+xml'}),
            '_rels/.rels': _rels({'rId1': (RT + 'officeDocument', 'ppt/presentation.xml')}),
            'ppt/presentation.xml': f'<p:presentation xmlns:a="{a}" xmlns:r="{NS_OD}" xmlns:p="{p}">'
                                    '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
                                    '<p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>'
                                    '<p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>',
            'ppt/_rels/presentation.xml.rels': _rels({'rId1': (RT + 'slideMaster', 'slideMasters/slideMaster1.xml'),
                                                      'rId2': (RT + 'slide', 'slides/slide1.xml'),
                                                      'rId3': (RT + 'theme', 'theme/theme1.xml')}),
            'ppt/slideMasters/slideMaster1.xml': f'<p:sldMaster xmlns:a="{a}" xmlns:r="{NS_OD}" xmlns:p="{p}">{tree}'
                                                 '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
                                                 'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
                                                 'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
                                                 '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
                                                 '</p:sldMaster>',
            'ppt/slideMasters/_rels/slideMaster1.xml.rels': _rels({'rId1': (RT + 'slideLayout', '../slideLayouts/slideLayout1.xml'),
                                                                   'rId2': (RT + 'theme', '../theme/theme1.xml')}),
            'ppt/slideLayouts/slideLayout1.xml': f'<p:sldLayout xmlns:a="{a}" xmlns:r="{NS_OD}" xmlns:p="{p}" type="blank">{tree}'
                                                 '</p:sldLayout>',
            'ppt/slideLayouts/_rels/slideLayout1.xml.rels': _rels({'rId1': (RT + 'slideMaster', '../slideMasters/slideMaster1.xml')}),
            'ppt/slides/slide1.xml': f'<p:sld xmlns:a="{a}" xmlns:r="{NS_OD}" xmlns:p="{p}">{tree}</p:sld>',
            'ppt/slides/_rels/slide1.xml.rels': _rels({'rId1': (RT + 'slideLayout', '../slideLayouts/slideLayout1.xml')}),
            'ppt/theme/theme1.xml': _theme(a),
        })
    return None


def _theme(a: str) -> str:
    colors = ''.join(f'<a:{n}><a:srgbClr val="{v}"/></a:{n}>' for n, v in (
        ('dk2', '1F497D'), ('lt2', 'EEECE1'), ('accent1', '4F81BD'), ('accent2', 'C0504D'), ('accent3', '9BBB59'),
        ('accent4', '8064A2'), ('accent5', '4BACC6'), ('accent6', 'F79646'), ('hlink', '0000FF'), ('folHlink', '800080')))
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    line = '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
    return (f'<a:theme xmlns:a="{a}" name="Office"><a:themeElements><a:clrScheme name="Office">'
            '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
            f'{colors}</a:clrScheme><a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/>'
            '<a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/>'
            f'</a:minorFont></a:fontScheme><a:fmtScheme name="Office"><a:fillStyleLst>{fill}{fill}{fill}</a:fillStyleLst>'
            f'<a:lnStyleLst>{line}{line}{line}</a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>'
            '<a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
            f'<a:bgFillStyleLst>{fill}{fill}{fill}</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>')

#!/usr/bin/env python3
"""Web search for Chat in the Hub, through DuckDuckGo: nothing to install,
no account, no key. The admin turns it on in Settings › AI.

The NAS asks DuckDuckGo, reads the first pages that come back and gives the
model the text with numbers, so it can answer with sources ([1], [2]).
That works with every model, also small ones without tools.

Pages are only read from addresses on the internet: never from this NAS or
the home network (a search result could point there on purpose).
"""

import ipaddress
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urljoin, urlparse

SEARCH_URLS = ('https://html.duckduckgo.com/html/', 'https://lite.duckduckgo.com/lite/')
AGENT = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
MAX_RESULTS = 6
READ_PAGES = 3
PAGE_CHARS = 2500
PAGE_BYTES = 1_500_000
MAX_QUERY = 300


class _Results(HTMLParser):
    """Titles, links and snippets out of DuckDuckGo's plain HTML pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._into: str | None = None   # 'title' or 'snippet'
        self._end = ''
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or '' for k, v in attrs}
        cls = a.get('class', '')
        if tag == 'a' and ('result__a' in cls or 'result-link' in cls):
            self.results.append({'title': '', 'url': _target(a.get('href', '')), 'snippet': ''})
            self._into, self._end, self._text = 'title', 'a', []
        elif ('result__snippet' in cls or 'result-snippet' in cls) and self.results:
            self._into, self._end, self._text = 'snippet', tag, []

    def handle_endtag(self, tag: str) -> None:
        if self._into and tag == self._end:
            self.results[-1][self._into] = re.sub(r'\s+', ' ', ''.join(self._text)).strip()
            self._into = None

    def handle_data(self, data: str) -> None:
        if self._into:
            self._text.append(data)


def _target(href: str) -> str:
    """DuckDuckGo links go through //duckduckgo.com/l/?uddg=<the address>."""
    if 'uddg=' in href:
        found = parse_qs(urlparse(href if '://' in href else 'https:' + href).query).get('uddg')
        if found:
            return unquote(found[0])
    return href if href.startswith(('http://', 'https://')) else ''


def parse_results(page: str) -> list[dict[str, str]]:
    parser = _Results()
    parser.feed(page)
    out: list[dict[str, str]] = []
    for r in parser.results:
        host = urlparse(r['url']).netloc
        if not r['url'] or not r['title'] or 'duckduckgo.com' in host:   # ads go through duckduckgo.com/y.js
            continue
        if all(o['url'] != r['url'] for o in out):
            out.append(r)
    return out[:MAX_RESULTS]


def search(query: str, post: Callable[..., Any] | None = None) -> tuple[list[dict[str, str]], str]:
    """The results for `query`, or why there are none."""
    if post is None:
        import requests
        post = requests.post
    query = re.sub(r'\s+', ' ', query).strip()[:MAX_QUERY]
    if not query:
        return [], 'Nothing to search for.'
    error = 'DuckDuckGo did not answer.'
    for url in SEARCH_URLS:
        try:
            res = post(url, data={'q': query, 'kl': 'wt-wt'}, headers={'User-Agent': AGENT}, timeout=12)
        except Exception:  # noqa: BLE001 - requests raises many kinds
            error = 'The NAS cannot reach DuckDuckGo.'
            continue
        if res.status_code >= 400:
            error = f'DuckDuckGo answered {res.status_code}.'
            continue
        found = parse_results(res.text)
        if found:
            return found, ''
        error = 'DuckDuckGo found nothing.'
    return [], error


def _public(url: str) -> bool:
    """Only addresses on the internet: not this NAS, not the home network."""
    parts = urlparse(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80))
    except (OSError, UnicodeError):
        return False
    return bool(infos) and all(ipaddress.ip_address(str(info[4][0]).split('%')[0]).is_global for info in infos)


class _Text(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'svg', 'nav', 'footer', 'header', 'form', 'aside', 'template'}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.SKIP:
            self.skip += 1
        elif tag in ('p', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'tr', 'div'):
            self.parts.append('\n')

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def page_text(html: str, limit: int = PAGE_CHARS) -> str:
    parser = _Text()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - broken pages
        pass
    lines = [re.sub(r'[ \t\r\f\v]+', ' ', line).strip() for line in ''.join(parser.parts).split('\n')]
    text = '\n'.join(line for line in lines if len(line) > 1)
    return text[:limit]


def read_page(url: str, get: Callable[..., Any] | None = None, public: Callable[[str], bool] = _public) -> str:
    """The readable text of a page ('' when it cannot be read)."""
    if get is None:
        import requests
        get = requests.get
    for _ in range(4):   # follows a few redirects, each checked again
        if not public(url):
            return ''
        try:
            res = get(url, headers={'User-Agent': AGENT, 'Accept': 'text/html'}, timeout=(5, 8),
                      allow_redirects=False, stream=True)
        except Exception:  # noqa: BLE001
            return ''
        try:
            if res.status_code in (301, 302, 303, 307, 308) and res.headers.get('Location'):
                url = urljoin(url, res.headers['Location'])
                continue
            if res.status_code >= 400 or 'html' not in res.headers.get('Content-Type', 'text/html'):
                return ''
            body = b''
            for chunk in res.iter_content(65536):
                body += chunk
                if len(body) > PAGE_BYTES:
                    break
            return page_text(body.decode(res.encoding or 'utf-8', errors='replace'))
        finally:
            res.close()
    return ''


def gather(query: str, post: Callable[..., Any] | None = None, get: Callable[..., Any] | None = None,
           public: Callable[[str], bool] = _public) -> tuple[list[dict[str, str]], str]:
    """Search, then read the first pages: [{title, url, snippet, text}]."""
    results, error = search(query, post)
    if not results:
        return [], error
    with ThreadPoolExecutor(max_workers=READ_PAGES) as pool:
        texts = list(pool.map(lambda r: read_page(r['url'], get, public), results[:READ_PAGES]))
    for r, text in zip(results, texts, strict=False):   # only the first ones were read
        r['text'] = text
    return results, ''


def context(results: list[dict[str, str]]) -> str:
    """What the model gets: numbered sources to answer from."""
    blocks = []
    for n, r in enumerate(results, 1):
        body = r.get('text') or r.get('snippet') or ''
        blocks.append(f'[{n}] {r["title"]}\n{r["url"]}\n{body}')
    return ('Results of a web search for the last question, as numbered sources. Answer from them where they '
            'help, and name the source of a fact with its number in brackets, like [1] or [2][3]. If they do '
            'not answer the question, say so. Do not make up sources.\n\n' + '\n\n'.join(blocks))

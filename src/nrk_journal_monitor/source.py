from __future__ import annotations

import random
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from curl_cffi.requests import RequestsError as CurlRequestsError
from curl_cffi.requests import Session as CurlSession

from .models import JournalObservation


SOURCE_URL = "https://info.nrk.no/innsyn/"
MAX_RESPONSE_BYTES = 2_000_000
MAX_REDIRECTS = 5
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_BLOCK_TAGS = {"article", "div", "li", "p", "section", "td"}
_IGNORED_TAGS = {"script", "style", "template", "nrkno-header"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_GENERIC_DOWNLOAD = re.compile(r"^(last\s+ned|download)(?:\s+(?:pdf|fil))?$", re.I)
_DATE_PERIOD = re.compile(
    r"(?P<start>\d{1,2}[./-]\d{1,2}[./-]\d{4})\s*"
    r"(?:–|—|-|til)\s*"
    r"(?P<end>\d{1,2}[./-]\d{1,2}[./-]\d{4})",
    re.I,
)


class SourceError(RuntimeError):
    pass


def _clean_text(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


def _is_hidden(attrs: dict[str, str]) -> bool:
    if "hidden" in attrs:
        return True
    if attrs.get("aria-hidden", "").lower() == "true":
        return True
    style = re.sub(r"\s+", "", attrs.get("style", "").lower())
    return "display:none" in style or "visibility:hidden" in style


@dataclass(slots=True)
class _Context:
    parts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Anchor:
    href: str
    parts: list[str]
    context: _Context


class _JournalHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[_Anchor] = []
        self._contexts: list[_Context] = [_Context()]
        self.root_context = self._contexts[0]
        self._tag_stack: list[tuple[str, bool, bool]] = []
        self._suppressed = 0
        self._anchor: _Anchor | None = None

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs = {key.lower(): value or "" for key, value in attrs_list}
        suppressed_here = tag in _IGNORED_TAGS or _is_hidden(attrs)
        if suppressed_here:
            self._suppressed += 1
        opened_context = tag in _BLOCK_TAGS and self._suppressed == 0
        if opened_context:
            self._contexts.append(_Context())
        self._tag_stack.append((tag, suppressed_here, opened_context))
        if tag == "a" and self._suppressed == 0:
            self._anchor = _Anchor(attrs.get("href", ""), [], self._contexts[-1])
        if tag in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_startendtag(
        self, tag: str, attrs_list: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs_list)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._anchor is not None:
            self.anchors.append(self._anchor)
            self._anchor = None
        if not self._tag_stack:
            if self._suppressed:
                return
            return
        stack_tag = self._tag_stack[-1][0]
        if stack_tag != tag and self._suppressed:
            matching_index = next(
                (
                    index
                    for index in range(len(self._tag_stack) - 1, -1, -1)
                    if self._tag_stack[index][0] == tag
                ),
                None,
            )
            if matching_index is None:
                return
            while len(self._tag_stack) - 1 > matching_index:
                self._close_stack_entry(self._tag_stack.pop())
        stack_tag, suppressed_here, opened_context = self._tag_stack.pop()
        if stack_tag != tag:
            # Malformed markup is ambiguous for a safety-sensitive source.
            raise SourceError("NRK returned malformed HTML")
        self._close_stack_entry((stack_tag, suppressed_here, opened_context))

    def _close_stack_entry(self, entry: tuple[str, bool, bool]) -> None:
        _tag, suppressed_here, opened_context = entry
        if opened_context:
            context = self._contexts.pop()
            self._contexts[-1].parts.extend(context.parts)
        if suppressed_here:
            self._suppressed -= 1

    def handle_data(self, data: str) -> None:
        if self._suppressed or not data.strip():
            return
        self._contexts[-1].parts.append(data)
        if self._anchor is not None:
            self._anchor.parts.append(data)

    def close(self) -> None:
        super().close()
        if self._tag_stack or self._suppressed or self._anchor is not None:
            raise SourceError("NRK returned incomplete HTML")


def _allowed_nrk_url(candidate: str) -> bool:
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and bool(host)
        and (host == "nrk.no" or host.endswith(".nrk.no"))
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    )


def _parse_norwegian_date(value: str) -> str:
    normalized = value.replace("/", ".").replace("-", ".")
    day, month, year = (int(part) for part in normalized.split("."))
    return date(year, month, day).isoformat()


def _period_from_text(value: str) -> tuple[str, str] | None:
    match = _DATE_PERIOD.search(value)
    if match is None:
        return None
    try:
        start = _parse_norwegian_date(match.group("start"))
        end = _parse_norwegian_date(match.group("end"))
    except ValueError:
        return None
    if end < start:
        return None
    return start, end


def parse_journal_page(html: str, *, page_url: str = SOURCE_URL) -> list[JournalObservation]:
    if not html.strip():
        raise SourceError("NRK returned an empty page")
    parser = _JournalHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError("Could not parse NRK journal page") from exc

    observations: dict[str, JournalObservation] = {}
    candidate_count = 0
    for anchor in parser.anchors:
        link_text = _clean_text(anchor.parts)
        context_text = _clean_text(anchor.context.parts)
        direct_journal = "journal" in link_text.casefold()
        generic_download = bool(_GENERIC_DOWNLOAD.fullmatch(link_text))
        contextual_journal = (
            generic_download
            and anchor.context is not parser.root_context
            and "journal" in context_text.casefold()
        )
        if not (direct_journal or contextual_journal):
            continue
        candidate_count += 1

        absolute_url = urljoin(page_url, anchor.href)
        if not anchor.href or not _allowed_nrk_url(absolute_url):
            raise SourceError("A journal candidate points outside the allowed NRK domain")

        identity_text = link_text if direct_journal else context_text
        period = _period_from_text(identity_text)
        if period is None:
            raise SourceError("A journal candidate has no stable date period")
        date_from, date_to = period
        title = identity_text
        observation = JournalObservation(date_from, date_to, title, absolute_url)
        previous = observations.get(observation.identity)
        if previous is not None and previous.source_url != observation.source_url:
            raise SourceError("A journal period points to multiple different files")
        observations[observation.identity] = observation

    if candidate_count == 0 or not observations:
        raise SourceError("No public journal links were found on the NRK page")
    return sorted(observations.values(), key=lambda item: (item.date_from, item.date_to))


@dataclass(frozen=True, slots=True)
class _BrowserResult:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    url: str


BrowserRequest = Callable[[str], _BrowserResult]


def _curl_browser_request(url: str) -> _BrowserResult:
    with CurlSession(impersonate="chrome") as session:
        response = session.get(
            url,
            timeout=(5.0, 15.0),
            allow_redirects=False,
            stream=True,
        )
        body = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise SourceError("NRK response exceeded the size limit")
        finally:
            response.close()
        return _BrowserResult(
            status_code=response.status_code,
            headers=response.headers,
            body=bytes(body),
            url=str(response.url),
        )


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.casefold()
    return next(
        (value for key, value in headers.items() if key.casefold() == wanted),
        "",
    )


class NrkJournalSource:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        max_attempts: int = 3,
        browser_request: BrowserRequest = _curl_browser_request,
    ) -> None:
        self._sleep = sleep
        self._jitter = jitter
        self._max_attempts = max_attempts
        self._browser_request = browser_request
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
            transport=transport,
            follow_redirects=False,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.5",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> NrkJournalSource:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _get(self) -> tuple[bytes, str]:
        url = SOURCE_URL
        redirects = 0
        for attempt in range(self._max_attempts):
            while True:
                if not _allowed_nrk_url(url):
                    raise SourceError("NRK redirect left the allowed domain")
                try:
                    with self._client.stream("GET", url) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("Location", "")
                            redirects += 1
                            if redirects > MAX_REDIRECTS or not location:
                                raise SourceError("NRK returned an invalid redirect chain")
                            url = urljoin(url, location)
                            continue
                        if response.status_code in RETRYABLE_STATUS_CODES:
                            if attempt + 1 == self._max_attempts:
                                raise SourceError(
                                    f"NRK remained unavailable (HTTP {response.status_code})"
                                )
                            break
                        if response.status_code == 403:
                            return self._get_with_browser(url, redirects=redirects)
                        if response.status_code != 200:
                            raise SourceError(f"NRK request failed (HTTP {response.status_code})")
                        content_type = response.headers.get("Content-Type", "").lower()
                        if "text/html" not in content_type:
                            raise SourceError("NRK returned a non-HTML response")
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_RESPONSE_BYTES:
                                raise SourceError("NRK response exceeded the size limit")
                        return bytes(body), str(response.url)
                except SourceError:
                    raise
                except (httpx.TimeoutException, httpx.NetworkError):
                    if attempt + 1 == self._max_attempts:
                        raise SourceError("NRK request failed after temporary errors") from None
                    break
            self._sleep(min(4.0, 0.5 * (2**attempt)) + self._jitter() * 0.25)
        raise SourceError("NRK request did not complete")

    def _get_with_browser(self, url: str, *, redirects: int) -> tuple[bytes, str]:
        for attempt in range(self._max_attempts):
            while True:
                if not _allowed_nrk_url(url):
                    raise SourceError("NRK redirect left the allowed domain")
                try:
                    result = self._browser_request(url)
                    if result.status_code in {301, 302, 303, 307, 308}:
                        location = _header(result.headers, "Location")
                        redirects += 1
                        if redirects > MAX_REDIRECTS or not location:
                            raise SourceError("NRK returned an invalid redirect chain")
                        url = urljoin(url, location)
                        continue
                    if result.status_code in RETRYABLE_STATUS_CODES:
                        if attempt + 1 == self._max_attempts:
                            raise SourceError(
                                f"NRK remained unavailable (HTTP {result.status_code})"
                            )
                        break
                    if result.status_code != 200:
                        raise SourceError(
                            f"NRK request failed (HTTP {result.status_code})"
                        )
                    if "text/html" not in _header(
                        result.headers, "Content-Type"
                    ).lower():
                        raise SourceError("NRK returned a non-HTML response")
                    if len(result.body) > MAX_RESPONSE_BYTES:
                        raise SourceError("NRK response exceeded the size limit")
                    if not _allowed_nrk_url(result.url):
                        raise SourceError("NRK response left the allowed domain")
                    return result.body, result.url
                except SourceError:
                    raise
                except (CurlRequestsError, OSError):
                    if attempt + 1 == self._max_attempts:
                        raise SourceError(
                            "NRK request failed after temporary errors"
                        ) from None
                    break
            self._sleep(min(4.0, 0.5 * (2**attempt)) + self._jitter() * 0.25)
        raise SourceError("NRK request did not complete")

    def fetch_journals(self) -> list[JournalObservation]:
        body, final_url = self._get()
        try:
            html = body.decode("utf-8")
        except UnicodeDecodeError:
            raise SourceError("NRK returned invalid UTF-8 HTML") from None
        return parse_journal_page(html, page_url=final_url)

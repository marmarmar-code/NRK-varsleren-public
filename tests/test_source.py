import httpx
import pytest

from nrk_journal_monitor.source import (
    NrkJournalSource,
    SourceError,
    _BrowserResult,
    parse_journal_page,
)


def test_parses_visible_journal_link() -> None:
    items = parse_journal_page(
        '<p><a href="/docs/journal.pdf">Offentlig journal 01.08.2026–07.08.2026</a></p>'
    )
    assert [(item.date_from, item.date_to) for item in items] == [
        ("2026-08-01", "2026-08-07")
    ]
    assert items[0].source_url == "https://info.nrk.no/docs/journal.pdf"


def test_recovers_single_extra_digit_in_start_year() -> None:
    items = parse_journal_page(
        '<p><a href="/docs/journal.pdf">Offentlig journal 17.08.20206–23.08.2026</a></p>'
    )
    assert [(item.date_from, item.date_to) for item in items] == [
        ("2026-08-17", "2026-08-23")
    ]


def test_rejects_unrecoverable_five_digit_year() -> None:
    with pytest.raises(SourceError, match="stable date period"):
        parse_journal_page(
            '<p><a href="/docs/journal.pdf">Offentlig journal 17.08.21206–23.08.2026</a></p>'
        )


def test_generic_download_uses_journal_context() -> None:
    items = parse_journal_page(
        '<div><span>Offentlig journal 1.8.2026 - 7.8.2026</span> '
        '<a href="https://www.nrk.no/a.pdf">Last ned</a></div>'
    )
    assert items[0].date_from == "2026-08-01"


def test_non_generic_link_does_not_borrow_nearby_period() -> None:
    with pytest.raises(SourceError, match="stable date period"):
        parse_journal_page(
            '<div>01.08.2026–07.08.2026 <a href="/a.pdf">Offentlig journal</a></div>'
        )


def test_hidden_and_non_content_links_are_ignored() -> None:
    html = """
    <div hidden><a href="https://evil.example/a.pdf">Journal uten dato</a></div>
    <script><a href="https://evil.example/b.pdf">Journal uten dato</a></script>
    <style><a href="https://evil.example/c.pdf">Journal uten dato</a></style>
    <template><a href="https://evil.example/d.pdf">Journal uten dato</a></template>
    <p><a href="/ok.pdf">Offentlig journal 01.08.2026–07.08.2026</a></p>
    """
    assert len(parse_journal_page(html)) == 1


def test_malformed_suppressed_site_header_is_ignored() -> None:
    html = """
    <nrkno-header><div><nav></div></nrkno-header>
    <main><p><a href="/ok.pdf">Journal 01.08.2026–07.08.2026</a></p></main>
    """
    assert len(parse_journal_page(html)) == 1


def test_one_ambiguous_candidate_makes_whole_page_fail() -> None:
    with pytest.raises(SourceError, match="stable date period"):
        parse_journal_page(
            '<p><a href="/ok.pdf">Journal 01.08.2026–07.08.2026</a></p>'
            '<p><a href="/bad.pdf">Offentlig journal</a></p>'
        )


def test_one_external_candidate_makes_whole_page_fail() -> None:
    with pytest.raises(SourceError, match="outside"):
        parse_journal_page(
            '<p><a href="/ok.pdf">Journal 01.08.2026–07.08.2026</a></p>'
            '<p><a href="https://example.com/bad.pdf">Journal 08.08.2026–14.08.2026</a></p>'
        )


def test_page_without_candidates_fails_closed() -> None:
    with pytest.raises(SourceError, match="No public journal links"):
        parse_journal_page('<p><a href="/other.pdf">Årsrapport 2026</a></p>')


def test_source_follows_bounded_same_domain_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/innsyn/":
            return httpx.Response(302, headers={"Location": "/innsyn/list"})
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text='<a href="/a.pdf">Journal 01.08.2026–07.08.2026</a>',
        )

    with NrkJournalSource(transport=httpx.MockTransport(handler)) as source:
        assert len(source.fetch_journals()) == 1


def test_source_rejects_redirect_outside_nrk() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(302, headers={"Location": "https://example.com/"})
    )
    with NrkJournalSource(transport=transport) as source:
        with pytest.raises(SourceError, match="allowed domain"):
            source.fetch_journals()


def test_source_uses_browser_compatible_fallback_only_for_403() -> None:
    calls: list[str] = []

    def browser_request(url: str) -> _BrowserResult:
        calls.append(url)
        return _BrowserResult(
            200,
            {"content-type": "text/html; charset=utf-8"},
            b'<a href="/a.pdf">Journal 01.08.2026-07.08.2026</a>',
            url,
        )

    transport = httpx.MockTransport(lambda _request: httpx.Response(403))
    with NrkJournalSource(
        transport=transport, browser_request=browser_request
    ) as source:
        assert len(source.fetch_journals()) == 1
    assert calls == ["https://info.nrk.no/innsyn/"]


def test_source_does_not_hide_non_403_client_errors() -> None:
    def unexpected_browser_request(_url: str) -> _BrowserResult:
        raise AssertionError("browser fallback must not run")

    transport = httpx.MockTransport(lambda _request: httpx.Response(401))
    with NrkJournalSource(
        transport=transport, browser_request=unexpected_browser_request
    ) as source:
        with pytest.raises(SourceError, match="HTTP 401"):
            source.fetch_journals()


def test_browser_fallback_rejects_external_redirect() -> None:
    def browser_request(url: str) -> _BrowserResult:
        return _BrowserResult(302, {"Location": "https://example.com/"}, b"", url)

    transport = httpx.MockTransport(lambda _request: httpx.Response(403))
    with NrkJournalSource(
        transport=transport, browser_request=browser_request
    ) as source:
        with pytest.raises(SourceError, match="allowed domain"):
            source.fetch_journals()


def test_browser_fallback_enforces_response_size_limit() -> None:
    def browser_request(url: str) -> _BrowserResult:
        return _BrowserResult(
            200,
            {"Content-Type": "text/html"},
            b"x" * 2_000_001,
            url,
        )

    transport = httpx.MockTransport(lambda _request: httpx.Response(403))
    with NrkJournalSource(
        transport=transport, browser_request=browser_request
    ) as source:
        with pytest.raises(SourceError, match="size limit"):
            source.fetch_journals()

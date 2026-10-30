"""Opt-in browser tests for the dashboard's complete gap list."""

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from jinja2 import Environment

WEB = Path(__file__).resolve().parents[1] / "src/mediaforge/web"
pytestmark = pytest.mark.skipif(
    os.environ.get("MEDIAFORGE_BROWSER_TESTS") != "1", reason="opt-in browser tests",
)


@pytest.fixture()
def stats_page():
    from patchright.sync_api import sync_playwright

    body = Environment().from_string(
        (WEB / "templates/_stats_body.html").read_text(encoding="utf-8"),
    ).render(_=lambda s: s)
    css = "\n".join((WEB / "static" / name).read_text(encoding="utf-8") for name in (
        "variables.css", "base.css", "forms.css", "buttons.css", "cards.css",
        "modals.css", "stats.css",
    ))
    # Omit media statistics deliberately: the direct link must not require them.
    payload = {"general": {}, "queue": {}, "sync": {}}
    items = [{"folder": f"series-{i}", "title": f"Series {i:02}", "location": "Test",
              "missing": [f"S1E{n}" for n in range(1, 28)]} for i in range(25)]
    requests = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, executable_path=os.environ.get("MEDIAFORGE_TEST_BROWSER"),
            timeout=15000,
        )
        page = browser.new_page()

        def respond(route):
            url = urlparse(route.request.url)
            if url.path == "/stats":
                route.fulfill(content_type="text/html", body=
                              '<meta charset="utf-8"><style>' + css + '</style>' + body +
                              '<script>window.t=(de,en)=>de;</script>' +
                              '<script src="/static/mf-charts.js"></script>' +
                              '<script src="/static/stats.js"></script>')
            elif url.path.startswith("/static/"):
                route.fulfill(content_type="application/javascript", body=
                              (WEB / "static" / url.path.rsplit("/", 1)[-1]).read_text(encoding="utf-8"))
            elif url.path == "/api/stats":
                route.fulfill(content_type="application/json", body=json.dumps(payload))
            elif url.path == "/api/media/incomplete":
                query = parse_qs(url.query)
                requests.append(query)
                number = int(query.get("page", ["1"])[0])
                size = int(query.get("per_page", ["20"])[0])
                route.fulfill(content_type="application/json", body=json.dumps({
                    "items": items[(number - 1) * size:number * size],
                    "page": number, "per_page": size, "total": len(items), "total_pages": 2,
                    "summary": {"incomplete": 25, "ignored": 0, "complete": 0, "missing_slots": 675},
                }))
            else:
                route.fulfill(status=404, body="")

        page.route("**/*", respond)
        yield page, requests
        browser.close()


def test_direct_link_opens_all_gap_pages_and_all_episode_slots(stats_page):
    page, requests = stats_page
    page.goto("http://mediaforge.test/stats#incomplete")
    page.locator("#incompleteBody tr").first.wait_for()
    assert page.locator("#incompleteModal").is_visible()
    assert page.locator("#incompleteBody tr").count() == 20
    assert page.locator("#incompleteBody tr").first.locator(".ignore-slot-chip").count() == 27
    page.locator('#incompleteTableHost .mf-pagination-page[data-page="2"]').click()
    page.wait_for_function("document.querySelectorAll('#incompleteBody tr').length === 5")
    assert "Series 24" in page.locator("#incompleteBody").inner_text()
    assert [r["page"] for r in requests] == [["1"], ["2"]]


def test_plain_statistics_page_keeps_gap_modal_closed(stats_page):
    page, requests = stats_page
    page.goto("http://mediaforge.test/stats")
    page.locator("#statsContent .stats-hint").first.wait_for()
    assert page.locator("#incompleteModal").is_hidden()
    assert not requests


def test_hash_link_resets_previous_filters_and_ignored_tab(stats_page):
    page, requests = stats_page
    page.goto("http://mediaforge.test/stats")
    page.locator("#statsContent .stats-hint").first.wait_for()
    page.evaluate("""() => {
        window._incompleteView = 'ignored';
        _modalView.incomplete.q = 'old search';
        _modalView.incomplete.page = 2;
        location.hash = 'incomplete';
    }""", isolated_context=False)
    page.locator("#incompleteBody tr").first.wait_for()
    assert page.locator("#incompleteModal").is_visible()
    assert requests[0]["page"] == ["1"]
    assert requests[0]["view"] == ["incomplete"]
    assert "q" not in requests[0]

"""Opt-in browser checks: MEDIAFORGE_BROWSER_TESTS=1 pytest tests/test_home_gaps_browser.py.

Uses Patchright's Chromium, or MEDIAFORGE_TEST_BROWSER for a local executable.
All provider and queue requests are intercepted; no downloads are started.
"""

import json
import os
import re
from pathlib import Path

import pytest
from jinja2 import Environment

WEB = Path(__file__).resolve().parents[1] / "src/mediaforge/web"
pytestmark = pytest.mark.skipif(
    os.environ.get("MEDIAFORGE_BROWSER_TESTS") != "1",
    reason="opt-in browser tests",
)


@pytest.fixture()
def gap_page():
    from patchright.sync_api import sync_playwright

    downloads = []
    responses = {
        "/api/search": {"results": [{"title": "Example", "url": "https://source.test/example"}]},
        "/api/series": {"title": "Example", "imdb_id": "tt41298100"},
        "/api/seasons": {"seasons": [
            {"season_number": 1, "url": "https://source.test/s1"},
            {"season_number": 2, "url": "https://source.test/s2"},
        ]},
        "/api/episodes": {"episodes": [
            {"episode_number": 1, "url": "https://source.test/e1", "downloaded": True,
             "languages": ["German Dub"]},
            {"episode_number": 2, "url": "https://source.test/e2",
             "languages": ["German Dub", "English Sub"]},
            {"episode_number": 3, "url": "https://source.test/e3",
             "languages": ["English Sub"]},
            {"episode_number": 4, "url": "https://source.test/e4",
             "languages": ["German Dub"]},
        ]},
        "/api/providers": {"providers": {"German Dub": ["VOE"], "English Sub": ["VOE"]}},
        "/api/download": {"queue_id": 42},
    }
    template = (WEB / "templates/_gap_fill_modal.html").read_text(encoding="utf-8")
    html = Environment().from_string(template).render(_=lambda s: s)
    css = "\n".join((WEB / "static" / name).read_text(encoding="utf-8") for name in (
        "variables.css", "base.css", "forms.css", "buttons.css",
        "modals.css", "shared_modals.css", "home_gaps.css",
    ))
    with sync_playwright() as p:
        executable = os.environ.get("MEDIAFORGE_TEST_BROWSER")
        browser = p.chromium.launch(headless=True, executable_path=executable, timeout=15000)
        page = browser.new_page(viewport={"width": 390, "height": 844})

        def respond(route):
            path = route.request.url.split("mediaforge.test", 1)[-1].split("?", 1)[0]
            if path == "/":
                route.fulfill(content_type="text/html", body=
                              '<meta charset="utf-8"><style>' + css +
                              '</style><button id="opener">Open</button>' + html)
                return
            if path not in responses:
                route.fulfill(status=404, body="")
                return
            if path == "/api/download":
                downloads.append(route.request.post_data_json)
            body = responses[path]
            if callable(body):
                body = body(route.request)
            route.fulfill(status=403 if body.get("error") else 200,
                          content_type="application/json", body=json.dumps(body))

        page.route("**/*", respond)
        page.goto("http://mediaforge.test/")
        page.add_script_tag(content="""
            window.t = (de, en) => de;
            window.loadSearchSources = async () => [{id: 'test', label: 'Test', enabled: true}];
            window.loadGeneralSettings = async () => ({});
            window._sourceIsOn = s => s.enabled;
        """)
        page.add_script_tag(content=(WEB / "static/home_gaps.js").read_text(encoding="utf-8"))
        yield page, responses, downloads
        browser.close()


def open_gap(page, **overrides):
    item = {"title": "Example", "missing_slots": ["S1E1", "S1E2", "S1E3"]}
    item.update(overrides)
    page.evaluate("item => window.openGapFill(item)", item, isolated_context=False)


def test_click_queues_only_missing_episodes_in_selected_language(gap_page):
    page, _, downloads = gap_page
    open_gap(page)
    assert page.locator("#gapFillEpisodes li").all_text_contents() == ["S1E2"]
    page.locator("#gapFillSubmit").click()
    page.wait_for_function("document.querySelector('#gapFillSubmit').textContent === 'Hinzugefügt'")
    assert downloads == [{
        "series_url": "https://source.test/example", "title": "Example",
        "episodes": ["https://source.test/e2"], "language": "German Dub", "provider": "VOE",
    }]
    assert page.locator("#gapFillSubmit").is_disabled()


def test_whole_season_respects_ignored_and_existing_episodes(gap_page):
    page, _, _ = gap_page
    open_gap(page, missing_slots=["S1"], ignored_slots=["S1E2"])
    assert page.locator("#gapFillEpisodes li").all_text_contents() == ["S1E4"]


def test_language_change_updates_episodes_before_queuing(gap_page):
    page, _, downloads = gap_page
    open_gap(page)
    page.locator("#gapFillLanguage").select_option("English Sub")
    page.wait_for_function("!document.querySelector('#gapFillSubmit').disabled")
    assert page.locator("#gapFillEpisodes li").all_text_contents() == ["S1E2", "S1E3"]
    page.locator("#gapFillSubmit").click()
    page.wait_for_function("document.querySelector('#gapFillSubmit').textContent === 'Hinzugefügt'")
    assert downloads[0]["episodes"] == ["https://source.test/e2", "https://source.test/e3"]


def test_unrelated_search_result_requires_explicit_choice(gap_page):
    page, responses, _ = gap_page
    responses["/api/search"]["results"][0]["title"] = "Example sequel"
    open_gap(page)
    assert page.locator("#gapFillSource").input_value() == ""
    assert page.locator("#gapFillSubmit").is_disabled()
    page.locator("#gapFillSource").select_option("0")
    page.wait_for_function("!document.querySelector('#gapFillSubmit').disabled")


def test_different_release_year_is_not_automatically_selected(gap_page):
    page, responses, _ = gap_page
    responses["/api/search"]["results"][0]["title"] = "Example (1990)"
    open_gap(page, title="Example (2020)")
    assert page.locator("#gapFillSource").input_value() == ""
    assert page.locator("#gapFillSubmit").is_disabled()


@pytest.mark.parametrize("folder, expected", [
    ("Reborn as a Space Mercenary I Woke Up Piloting the Strongest Starship! "
     "(2026-2026) [imdbid-tt41298100]",
     "Reborn as a Space Mercenary I Woke Up Piloting the Strongest Starship!"),
    ("Example (2026) [imdbid-tt41298100]", "Example"),
    ("Example (2020-2026) [tmdbid-1234] [tvdbid-5678]", "Example"),
    ("Example (2020–2026) {imdb-tt41298100}", "Example"),
    ("Example (2020-) [tmdb-1234]", "Example"),
    ("Example [imdbid-tt41298100] (2026)", "Example"),
    ("[Oshi no Ko] (2023-2026) [imdbid-tt21030032]", "[Oshi no Ko]"),
    ("Example (The Animation) [imdbid-tt41298100]", "Example (The Animation)"),
])
def test_search_strips_library_metadata_and_preserves_display_title(gap_page, folder, expected):
    page, responses, _ = gap_page
    searches = []
    page.on("request", lambda req: searches.append(req.post_data_json)
            if req.url.endswith("/api/search") else None)
    responses["/api/search"]["results"][0]["title"] = expected
    match = re.search(r"tt\d+", folder)
    if match:
        responses["/api/series"]["imdb_id"] = match[0]
    open_gap(page, title=folder)
    assert searches == [{"keyword": expected, "site": "test"}]
    assert page.locator("#gapFillTitle").inner_text() == "Lücken füllen: " + folder
    assert page.locator("#gapFillSubmit").is_enabled()


def test_alternative_provider_title_opens_matching_series_directly(gap_page):
    page, responses, _ = gap_page
    responses["/api/search"]["results"][0]["title"] = "Mezametara Saikyou"
    open_gap(page, title="Reborn as a Space Mercenary (2026-2026) [imdbid-tt41298100]")
    assert page.locator("#gapFillSource").input_value() == "0"
    assert page.locator("#gapFillEpisodes li").all_text_contents() == ["S1E2"]
    assert page.locator("#gapFillSubmit").is_enabled()


def test_imdb_mismatch_is_not_overridden_by_equal_title(gap_page):
    page, responses, _ = gap_page
    responses["/api/series"]["imdb_id"] = "tt99999999"
    open_gap(page, title="Example (2026) [imdbid-tt41298100]")
    assert page.locator("#gapFillSource").input_value() == ""
    assert page.locator("#gapFillSubmit").is_disabled()


def test_verified_series_is_selected_instead_of_first_search_result(gap_page):
    page, responses, _ = gap_page
    responses["/api/search"]["results"] = [
        {"title": "Example", "url": "https://source.test/wrong"},
        {"title": "Alternative title", "url": "https://source.test/correct"},
    ]
    responses["/api/series"] = lambda req: {
        "imdb_id": "tt41298100" if "correct" in req.url else "tt99999999",
    }
    open_gap(page, title="Example (2026) [imdbid-tt41298100]")
    assert page.locator("#gapFillSource").input_value() == "1"
    assert page.locator("#gapFillSubmit").is_enabled()


def test_failed_episode_lookup_cannot_queue_partial_data(gap_page):
    page, responses, downloads = gap_page
    responses["/api/episodes"] = {"error": "Source unavailable"}
    open_gap(page)
    assert "Source unavailable" in page.locator("#gapFillStatus").inner_text()
    assert page.locator("#gapFillSubmit").is_disabled()
    assert not downloads


def test_queue_refusal_stays_visible_and_does_not_claim_success(gap_page):
    page, responses, downloads = gap_page
    responses["/api/download"] = {"error": "not permitted"}
    open_gap(page)
    page.locator("#gapFillSubmit").click()
    page.wait_for_function("document.querySelector('#gapFillStatus').textContent.includes('not permitted')")
    assert len(downloads) == 1
    assert page.locator("#gapFillSubmit").is_disabled()
    assert page.locator("#gapFillClose").is_enabled()


def test_escape_restores_focus_and_scroll(gap_page):
    page, _, _ = gap_page
    page.locator("#opener").focus()
    open_gap(page)
    assert page.evaluate("document.body.style.overflow") == "hidden"
    page.keyboard.press("Escape")
    assert page.locator("#gapFillOverlay").is_hidden()
    assert page.evaluate("document.activeElement.id") == "opener"
    assert page.evaluate("document.body.style.overflow") == ""


@pytest.mark.parametrize("width", [390, 1280])
def test_dialog_fits_viewport_and_keeps_keyboard_focus(gap_page, width):
    page, _, _ = gap_page
    page.set_viewport_size({"width": width, "height": 844})
    open_gap(page)
    box = page.locator(".gap-fill-modal").bounding_box()
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= width
    assert box["y"] >= 0
    assert box["y"] + box["height"] <= 844
    page.locator("#gapFillSubmit").focus()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "gapFillClose"
    output = os.environ.get("MEDIAFORGE_SCREENSHOT_DIR")
    if output:
        page.screenshot(path=str(Path(output) / f"gap-fill-{width}.png"))

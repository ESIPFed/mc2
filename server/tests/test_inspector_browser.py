"""Inspector DOM/contract tests; rendering against real MapLibre is a smoke check.

Requires Playwright Chromium (`playwright install chromium`). Skips when the
optional browser is absent, so the API suite remains usable without browsers.
"""
from pathlib import Path

import pytest

STATIC = Path(__file__).parents[1] / "mapcontrol_server" / "static"


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        try:
            instance = p.chromium.launch(headless=True)
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Chromium is not installed; run playwright install chromium")
            raise
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    page = browser.new_page(viewport={"width": 900, "height": 650})
    page.route("http://fixture.test/**", lambda route: route.fulfill(
        content_type="text/html", body="<html><head></head><body><button>Inside chart</button></body></html>"
    ))
    page.set_content('<base href="http://fixture.test/map/example"><div id="map" style="width:900px;height:650px"></div>')
    page.evaluate("""() => {
      const container = document.querySelector('#map');
      const callbacks = {};
      window.drawing = false;
      window.clickEvents = [];
      window.hitAsset = 'a';
      const map = {
        on(name, fn) { (callbacks[name] ||= []).push(fn); },
        getLayer() { return {}; },
        getCanvas() { return container; }, getContainer() { return container; },
        queryRenderedFeatures() { return window.hitAsset ? [{ id: 'feature-1', layer: { id: window.hitAsset }, geometry: { type: 'Point', coordinates: [1, 2] } }] : []; }
      };
      const registry = { a: { layerIds: ['a'] }, b: { layerIds: ['b'] } };
      const assetInfo = {
        a: { asset_id: 'a', name: 'Station A', visible: true, metadata: { description: 'Sample description', extra: { inspector: { version: 1, status: 'loading', attachments: [] } } } },
        b: { asset_id: 'b', name: 'Plain asset', visible: true, metadata: {} }
      };
      const handlers = { update_metadata() {}, asset_updated() {}, set_visibility() {}, delete_asset() {} };
      window.__esipInternals = { map, registry, assetInfo, handlers, mapId: 'example', baseUrl: 'http://fixture.test', getUserSession: () => 'viewer-1', isDrawing: () => window.drawing,
        rememberAsset(data) { assetInfo[data.asset_id] = { ...assetInfo[data.asset_id], ...data }; }, deselectAsset() {} };
      window.maplibregl = { Popup: class {
        constructor() { this.el = document.createElement('div'); this.el.className = 'esip-inspector-popup'; }
        setLngLat(anchor) { window.popupAnchor = anchor; return this; }
        setDOMContent(content) { this.el.replaceChildren(content); return this; }
        addTo() { container.append(this.el); return this; }
        getElement() { return this.el; } setMaxWidth() { return this; }
        remove() { this.el.remove(); }
      } };
      window.clickAsset = id => { window.hitAsset = id; (callbacks.click || []).forEach(fn => fn({ point: { x: 60, y: 90 }, lngLat: { lng: 8, lat: 9 } })); };
      window.addEventListener('esip:asset_click', e => window.clickEvents.push(e.detail));
    }""")
    page.add_script_tag(path=str(STATIC / "esip-contract.js"))
    page.add_script_tag(path=str(STATIC / "esip-inspector.js"))
    yield page
    page.close()


def test_claim_anchor_lifecycle_and_live_metadata(page):
    page.evaluate("clickAsset('a')")
    assert page.locator(".esip-inspector-popup").count() == 1
    assert page.evaluate("popupAnchor") == [1, 2]
    assert page.evaluate("clickEvents.at(-1).inspector_handled") is True
    assert page.evaluate("clickEvents.at(-1).feature_id") == "feature-1"
    assert "prepared" in page.locator(".esip-inspector-status").inner_text()
    page.evaluate("""__esipInternals.handlers.update_metadata({ asset_id: 'a', metadata: {
      description: '<b>Plain text description</b>', extra: { inspector: { version: 1, status: 'ready', attachments: [
        { type: 'link', url: '/results/figure.html', title: 'View result' },
        { type: 'html', url: 'javascript:alert(1)' }, { type: 'image', url: 'data:image/svg+xml,bad' }
      ] } }
    } })""")
    assert page.locator(".esip-inspector-popup").count() == 1
    assert page.locator(".esip-inspector-description b").count() == 0
    assert not page.locator(".esip-inspector-description").is_visible()
    page.locator(".esip-inspector-details summary").click()
    assert page.locator(".esip-inspector-description").is_visible()
    assert page.locator(".esip-inspector-link").count() == 1
    assert page.locator(".esip-inspector-link").get_attribute("href") == "http://fixture.test/results/figure.html"
    assert page.locator("iframe, img").count() == 0
    page.get_by_role("button", name="Expand", exact=True).click()
    assert page.locator(".esip-inspector-popup.is-expanded").count() == 1
    page.evaluate("__esipInternals.handlers.set_visibility({asset_id:'a',visible:false})")
    assert page.locator(".esip-inspector-popup").count() == 0


def test_dismiss_switch_delete_draw_and_background(page):
    page.evaluate("clickAsset('a'); clickAsset('b')")
    assert page.locator(".esip-inspector-popup").count() == 0
    assert page.evaluate("clickEvents.at(-1).inspector_handled") is False
    page.evaluate("clickAsset('a')")
    page.keyboard.press("Escape")
    assert page.locator(".esip-inspector-popup").count() == 0
    page.evaluate("clickAsset('a'); clickAsset(null)")
    assert page.locator(".esip-inspector-popup").count() == 0
    page.evaluate("drawing=true; clickAsset('a')")
    assert page.locator(".esip-inspector-popup").count() == 0
    page.evaluate("drawing=false; clickAsset('a'); __esipInternals.handlers.delete_asset({asset_id:'a'})")
    assert page.locator(".esip-inspector-popup").count() == 0


def test_html_opaque_sandbox_and_escape_inside_chart(page):
    page.evaluate("""__esipInternals.handlers.update_metadata({ asset_id:'a', metadata: { extra: { inspector: { version:1, attachments:[{type:'html',url:'/figure.html'}] } } } }); clickAsset('a')""")
    frame = page.locator(".esip-inspector-frame")
    assert frame.get_attribute("sandbox") == "allow-scripts"
    page.wait_for_function("document.querySelector('.esip-inspector-frame').srcdoc.includes('esip-inspector-artifact')")
    page.frame_locator(".esip-inspector-frame").get_by_role("button", name="Inside chart").click()
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('.esip-inspector-popup')")


def test_auto_session_preserves_embed_options_and_hash(page):
    # Execute the actual bootstrap URL-rewrite block independently of tiles
    # and WebSocket timing, so this regression needs no live tile provider.
    source = (STATIC.parent / "main.py").read_text()
    block = source.split("window.USER_SESSION_AUTO = data.user_session_id;", 1)[1]
    block = block.split("// Rebuild WS URL with the new session", 1)[0]
    result = page.evaluate("""script => {
      const start = 'https://maps.test/map/example?ui=controls&inspect=1&theme=dark&basemap=satellite#station-a';
      let replaced = null;
      const window = { location: { href: start, pathname: '/map/example' }, history: { replaceState(_state, _title, url) { replaced = String(url); } } };
      const data = { user_session_id: 'viewer-new' };
      eval(script);
      const url = new URL(replaced, start);
      return { params: Object.fromEntries(url.searchParams), hash: url.hash, pathname: url.pathname };
    }""", block)
    assert result == {
        "params": {"ui": "controls", "inspect": "1", "theme": "dark", "basemap": "satellite", "user_session": "viewer-new"},
        "hash": "#station-a", "pathname": "/map/example",
    }



def test_mobile_sheet_overrides_maplibre_top_position(page):
    page.set_viewport_size({"width": 390, "height": 750})
    # MapLibre's baseline top:0 must be explicitly released for a bottom sheet.
    page.add_style_tag(content=".esip-inspector-popup { position:absolute; top:0; left:0; }")
    page.add_style_tag(path=str(STATIC / "esip-inspector.css"))
    page.evaluate("document.querySelector('#map').style.cssText='position:relative;width:390px;height:750px'; document.body.style.margin='0'; clickAsset('a')")
    popup = page.locator(".esip-inspector-popup")
    bounds = popup.bounding_box()
    assert bounds is not None
    assert bounds["y"] + bounds["height"] == pytest.approx(742, abs=1)
    page.get_by_role("button", name="Expand", exact=True).click()
    assert popup.bounding_box()["y"] == pytest.approx(8, abs=1)

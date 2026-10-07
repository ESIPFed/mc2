"""Browser coverage for chart preview, pinning, dragging, and cached upgrades.

The map stub provides projection and input boundaries; a separate real MapLibre
smoke check covers renderer integration. Requires optional Playwright Chromium.
"""
from pathlib import Path
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

import pytest

STATIC = Path(__file__).parents[1] / "mapcontrol_server" / "static"


MAP_SETUP_JS = """() => {
  const container = document.querySelector('#map');
  const canvas = document.createElement('div');
  canvas.id = 'canvas'; canvas.style.cssText = 'position:absolute;inset:0'; container.append(canvas);
  container.style.position = 'relative';
  const callbacks = {};
  window.drawing = false;
  window.clickEvents = [];
  window.hitAsset = 'a';
  window.mapInputs = { pointerdown: 0, wheel: 0 };
  for (const name of Object.keys(mapInputs)) container.addEventListener(name, () => mapInputs[name]++);
  window.projection = { x: 0, y: 0, scale: 1 };
  const coordinates = { a: [1, 2], b: [8, 5], c: [5, 3] };
  const emit = (name, detail = {}) => (callbacks[name] || []).forEach(fn => fn(detail));
  const map = {
    on(name, fn) { (callbacks[name] ||= []).push(fn); },
    off(name, fn) { callbacks[name] = (callbacks[name] || []).filter(cb => cb !== fn); },
    getLayer() { return {}; },
    getCanvas() { return canvas; }, getCanvasContainer() { return container; }, getContainer() { return container; },
    project(anchor) { const [x,y] = Array.isArray(anchor) ? anchor : [anchor.lng, anchor.lat]; return { x: x * 100 * projection.scale + projection.x, y: y * 100 * projection.scale + projection.y }; },
    unproject(point) { return { lng: (point.x - projection.x) / (100 * projection.scale), lat: (point.y - projection.y) / (100 * projection.scale) }; },
    dragPan: { _enabled: true, disable() { this._enabled = false; }, enable() { this._enabled = true; }, isEnabled() { return this._enabled; } },
    queryRenderedFeatures() { return window.hitAsset ? [{ id: 'feature-' + hitAsset, layer: { id: hitAsset }, geometry: { type: 'Point', coordinates: coordinates[hitAsset] } }] : []; }
  };
  const registry = { a: { layerIds: ['a'], name: 'Station A' }, b: { layerIds: ['b'] }, c: { layerIds: ['c'], name: 'Station C' } };
  const loading = { version: 1, status: 'loading', attachments: [] };
  const assetInfo = {
    a: { asset_id: 'a', name: 'Station A', visible: true, metadata: { description: 'Sample description', extra: { inspector: loading } } },
    b: { asset_id: 'b', name: 'Plain asset', visible: true, metadata: {} },
    c: { asset_id: 'c', name: 'Station C', visible: true, metadata: { extra: { inspector: loading } } }
  };
  const handlers = { update_metadata() {}, asset_updated() {}, set_visibility() {}, delete_asset() {} };
  window.__esipInternals = { map, registry, assetInfo, handlers, mapId: 'example', baseUrl: 'http://fixture.test', getUserSession: () => 'viewer-1', isDrawing: () => window.drawing,
    rememberAsset(data) { assetInfo[data.asset_id] = { ...assetInfo[data.asset_id], ...data }; }, deselectAsset() {} };
  // Kept for compatibility with the pre-callout browser-cache fixture.
  window.maplibregl = { Popup: class {
    constructor() { this.el = document.createElement('div'); this.el.className = 'esip-inspector-popup'; }
    setLngLat(anchor) { window.popupAnchor = anchor; return this; }
    setDOMContent(content) { this.el.replaceChildren(content); return this; }
    addTo() { container.append(this.el); return this; }
    getElement() { return this.el; } setMaxWidth() { return this; }
    remove() { this.el.remove(); }
  } };
  const featureEvent = id => {
    const coords = coordinates[id] || [8, 5];
    return { point: map.project(coords), lngLat: { lng: coords[0], lat: coords[1] } };
  };
  window.clickAsset = id => { window.hitAsset = id; emit('click', featureEvent(id)); };
  window.hoverAsset = id => { window.hitAsset = id; emit('mousemove', featureEvent(id)); };
  window.leaveMap = () => emit('mouseout');
  window.moveMap = (x, y, scale = 1) => { Object.assign(projection, { x, y, scale }); emit('move'); };
  window.resizeMap = (width, height) => { container.style.width = width + 'px'; container.style.height = height + 'px'; emit('resize'); };
  window.addEventListener('esip:asset_click', e => window.clickEvents.push(e.detail));
}"""


CHART_HTML = """<!doctype html><html><head></head><body style="margin:0">
<button id="chart-action" onclick="this.textContent='Chart changed'">Inside chart</button>
<div id="plot" style="width:250px;height:150px;background:#eef" tabindex="0">Interactive plot</div>
<script>
const plot=document.querySelector('#plot');
plot.addEventListener('pointerdown',()=>{plot.dataset.dragged='yes';});
plot.addEventListener('wheel',event=>{event.preventDefault();plot.dataset.zoomed='yes';},{passive:false});
</script></body></html>"""


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
    page.route("http://fixture.test/**", lambda route: route.fulfill(content_type="text/html", body=CHART_HTML))
    page.set_content('<base href="http://fixture.test/map/example"><style>body{margin:0}</style><div id="map" style="width:900px;height:650px"></div>')
    page.evaluate(MAP_SETUP_JS)
    page.add_style_tag(path=str(STATIC / "esip-inspector.css"))
    page.add_script_tag(path=str(STATIC / "esip-contract.js"))
    page.add_script_tag(path=str(STATIC / "esip-inspector.js"))
    yield page
    page.close()


def card(page, asset_id="a"):
    return page.locator(f'.esip-inspector-card[data-asset-id="{asset_id}"]')


def chart_ready(page, asset_id="a"):
    page.evaluate("""id => __esipInternals.handlers.update_metadata({ asset_id: id, metadata: {
      title:'Repeated artifact title', description:'An interpretation that should not clutter the chart.',
      extra: { inspector: { version:1, status:'ready', attachments:[{type:'html',url:'/figure.html',title:'Repeated artifact title'}] } }
    } })""", asset_id)


def test_hover_preview_is_delayed_and_closes_on_leave(page):
    page.evaluate("hoverAsset('a')")
    page.wait_for_timeout(60)
    assert card(page).count() == 0
    card(page).wait_for(state="visible")
    assert "is-preview" in card(page).get_attribute("class")
    assert "preparing" in card(page).locator(".esip-inspector-status").inner_text().lower()
    assert page.evaluate("document.activeElement === document.body")
    page.evaluate("leaveMap()")
    card(page).wait_for(state="detached")
    # A brief pass over a station must not produce a late orphaned preview.
    page.evaluate("hoverAsset('a'); leaveMap()")
    page.wait_for_timeout(350)
    assert card(page).count() == 0


def test_hover_promotes_without_reloading_chart_and_supports_multiple_pins(page):
    chart_ready(page)
    page.evaluate("hoverAsset('a')")
    preview = card(page)
    preview.wait_for(state="visible")
    frame = preview.locator("iframe")
    frame.wait_for()
    frame.evaluate("frame => frame.dataset.chartState = 'preserved'")
    assert preview.evaluate("el => el.inert") is True
    assert frame.evaluate("frame => getComputedStyle(frame).pointerEvents") == "none"
    page.evaluate("clickAsset('a')")
    assert "is-pinned" in preview.get_attribute("class")
    assert preview.evaluate("el => el.inert") is False
    assert frame.get_attribute("data-chart-state") == "preserved"
    assert frame.evaluate("frame => getComputedStyle(frame).pointerEvents") != "none"
    assert page.evaluate("clickEvents.at(-1).inspector_handled") is True
    assert page.evaluate("clickEvents.at(-1).feature_id") == "feature-a"
    page.evaluate("hoverAsset('c')")
    card(page, "c").wait_for(state="visible")
    page.evaluate("clickAsset('c'); leaveMap(); clickAsset(null); clickAsset('b')")
    assert page.locator(".esip-inspector-card.is-pinned").count() == 2
    assert page.evaluate("clickEvents.at(-1).inspector_handled") is False
    assert preview.locator("h2").count() == 1
    assert preview.locator("h3,details,summary,a").count() == 0
    assert "Repeated artifact title" not in preview.inner_text()
    card(page, "c").locator(".esip-inspector-close").click()
    assert card(page, "c").count() == 0
    assert card(page).count() == 1


def test_escape_dismisses_preview_then_topmost_pin(page):
    page.evaluate("clickAsset('a'); clickAsset('c')")
    page.keyboard.press("Escape")
    assert card(page, "c").count() == 0
    assert card(page).count() == 1
    page.evaluate("hoverAsset('c')")
    card(page, "c").wait_for(state="visible")
    page.keyboard.press("Escape")
    assert card(page, "c").count() == 0
    assert card(page).count() == 1
    page.keyboard.press("Escape")
    assert page.locator(".esip-inspector-card").count() == 0


def test_live_updates_hide_and_delete_only_affected_pin(page):
    page.evaluate("clickAsset('a'); clickAsset('c')")
    second = card(page, "c")
    second.evaluate("el => el.dataset.untouched = 'yes'")
    before = card(page).bounding_box()
    chart_ready(page)
    assert card(page).locator("iframe").count() == 1
    assert card(page).bounding_box()["x"] == pytest.approx(before["x"])
    assert card(page).bounding_box()["y"] == pytest.approx(before["y"])
    assert second.get_attribute("data-untouched") == "yes"
    page.evaluate("__esipInternals.handlers.set_visibility({asset_id:'c',visible:false})")
    assert second.count() == 0
    assert card(page).count() == 1
    page.evaluate("__esipInternals.handlers.delete_asset({asset_id:'a'})")
    assert card(page).count() == 0
    assert page.locator(".esip-inspector-connectors line").count() == 0
    assert page.locator(".esip-inspector-anchor").count() == 0


def test_dragged_pins_stay_screen_fixed_while_connector_tracks_map_and_resize(page):
    page.evaluate("clickAsset('a')")
    selected = card(page)
    selected.wait_for(state="visible")
    header = selected.locator(".esip-inspector-header").bounding_box()
    initial = selected.bounding_box()
    page.mouse.move(header["x"] + 35, header["y"] + header["height"] / 2)
    page.mouse.down()
    page.mouse.move(header["x"] + 180, header["y"] + 75, steps=6)
    page.mouse.up()
    dragged = selected.bounding_box()
    assert dragged["x"] > initial["x"] + 70
    assert dragged["y"] > initial["y"] + 20
    assert page.evaluate("mapInputs.pointerdown") == 0
    line = page.locator('.esip-inspector-connectors line[data-asset-id="a"]')
    dot = page.locator('.esip-inspector-anchor[data-asset-id="a"]')
    assert float(line.get_attribute("x1")) == pytest.approx(100)
    assert float(line.get_attribute("y1")) == pytest.approx(200)
    assert float(dot.get_attribute("cx")) == pytest.approx(100)
    assert float(dot.get_attribute("cy")) == pytest.approx(200)
    page.evaluate("moveMap(35, 20, 1.25)")
    page.wait_for_function("Number(document.querySelector('.esip-inspector-connectors line[data-asset-id=\"a\"]').getAttribute('x1')) === 160")
    assert selected.bounding_box()["x"] == pytest.approx(dragged["x"])
    assert selected.bounding_box()["y"] == pytest.approx(dragged["y"])
    assert float(line.get_attribute("x1")) == pytest.approx(160)
    assert float(line.get_attribute("y1")) == pytest.approx(270)
    assert float(dot.get_attribute("cx")) == pytest.approx(160)
    assert float(dot.get_attribute("cy")) == pytest.approx(270)
    page.evaluate("resizeMap(390, 320)")
    page.wait_for_function("""() => {
      const bounds=document.querySelector('.esip-inspector-card[data-asset-id="a"]').getBoundingClientRect();
      return bounds.right <= 390 && bounds.bottom <= 320;
    }""")
    resized = selected.bounding_box()
    assert resized["x"] >= 0 and resized["y"] >= 0
    assert resized["x"] + resized["width"] <= 390
    assert resized["y"] + resized["height"] <= 320


def test_pinned_chart_receives_pointer_and_wheel_without_dragging_map(page):
    chart_ready(page)
    page.evaluate("clickAsset('a')")
    inner = page.frame_locator('.esip-inspector-card[data-asset-id="a"] iframe')
    inner.get_by_role("button", name="Inside chart").click()
    assert inner.get_by_role("button", name="Chart changed").count() == 1
    plot = inner.locator("#plot")
    plot.hover()
    page.mouse.down()
    page.mouse.move(200, 250, steps=3)
    page.mouse.up()
    plot.hover()
    page.mouse.wheel(0, 100)
    assert plot.get_attribute("data-dragged") == "yes"
    # Dispatch can be async relative to Playwright's mouse.wheel completion.
    inner.locator('#plot[data-zoomed="yes"]').wait_for(state="visible")
    assert page.evaluate("mapInputs") == {"pointerdown": 0, "wheel": 0}


def test_html_sandbox_and_source_checked_escape_bridge(page):
    chart_ready(page)
    page.evaluate("clickAsset('a'); clickAsset('c')")
    frame = card(page).locator("iframe")
    assert frame.get_attribute("sandbox") == "allow-scripts"
    page.wait_for_function("document.querySelector('.esip-inspector-frame').srcdoc.includes('esip-inspector-artifact')")
    page.evaluate("window.postMessage({source:'esip-inspector-artifact',token:'forged'}, '*')")
    assert card(page).count() == 1
    content_frame = frame.element_handle().content_frame()
    assert content_frame.evaluate("() => { try { parent.document.body; return false; } catch (_) { return true; } }")
    content_frame.get_by_role("button", name="Inside chart").click()
    page.keyboard.press("Escape")
    card(page).wait_for(state="detached")
    assert card(page, "c").count() == 1


def test_unsafe_urls_filtered_and_link_only_attachments_remain_available(page):
    page.evaluate("""__esipInternals.handlers.update_metadata({asset_id:'a',metadata:{extra:{inspector:{version:1,status:'ready',attachments:[
      {type:'html',url:'javascript:alert(1)'}, {type:'image',url:'data:image/svg+xml,bad'},
      {type:'link',url:'/results/figure.html',title:'View result'}
    ]}}}}); clickAsset('a')""")
    assert card(page).locator("iframe,img").count() == 0
    link = card(page).get_by_role("link", name="View result")
    assert link.get_attribute("href") == "http://fixture.test/results/figure.html"
    assert link.get_attribute("rel") == "noopener noreferrer"


def test_drawing_mode_suppresses_previews_and_pins(page):
    page.evaluate("drawing=true; hoverAsset('a'); clickAsset('a')")
    page.wait_for_timeout(350)
    assert page.locator(".esip-inspector-card").count() == 0


def test_auto_session_preserves_embed_options_and_hash(page):
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


def test_versioned_contract_recovers_inspector_with_warm_browser_cache(browser):
    """Reproduce an upgrade in one browser context, without disabling its cache.

    The fixture is the actual contract from 45a2dcf, before inspector support.
    A real HTTP server (not Playwright routing, which disables the cache)
    serves it as fresh cached content, then deploys the current UI. Only
    content-versioned URLs recover the inspector on an ordinary navigation.
    """
    from mapcontrol_server.main import _ui_asset_url

    previous = (Path(__file__).parent / "fixtures" / "esip-contract-before-inspector.js").read_bytes()
    requests = Counter()
    deployed = False

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests[self.path] += 1
            path = urlsplit(self.path).path
            if path.startswith("/static/"):
                filename = path.rsplit("/", 1)[1]
                payload = previous if filename == "esip-contract.js" and not deployed else (STATIC / filename).read_bytes()
                content_type = "text/css" if filename.endswith(".css") else "text/javascript"
                cache_control = "public, max-age=31536000, immutable"
            elif path in ("/before", "/after-bare", "/after-versioned"):
                versioned = path == "/after-versioned"
                asset_url = lambda filename: _ui_asset_url("", filename) if versioned else f"/static/{filename}"
                inspector = "" if path == "/before" else (
                    f'<link rel="stylesheet" href="{asset_url("esip-inspector.css")}">'
                    f'<script src="{asset_url("esip-inspector.js")}"></script>'
                )
                payload = (
                    '<!doctype html><div id="map" style="width:900px;height:650px"></div>'
                    f'<script>({MAP_SETUP_JS})();</script>'
                    f'<script src="{asset_url("esip-contract.js")}"></script>{inspector}'
                ).encode()
                content_type = "text/html"
                cache_control = "no-cache"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", cache_control)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = browser.new_context()
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        page.goto(base_url + "/before")
        assert page.evaluate("typeof ESIPMap.getAssetInfo") == "undefined"
        assert requests["/static/esip-contract.js"] == 1

        deployed = True
        page.goto(base_url + "/after-bare")
        page.evaluate("clickAsset('a')")
        assert page.evaluate("clickEvents.at(-1).asset_id") == "a"
        assert page.evaluate("clickEvents.at(-1).inspector_handled") is None
        assert page.locator(".esip-inspector-card").count() == 0
        assert requests["/static/esip-contract.js"] == 1  # Still the old browser cache entry.

        page.goto(base_url + "/after-versioned")
        page.evaluate("clickAsset('a')")
        assert page.evaluate("clickEvents.at(-1).inspector_handled") is True
        assert page.locator(".esip-inspector-card").count() == 1
        assert page.get_by_role("heading", name="Station A", exact=True).count() == 1
        assert requests[_ui_asset_url("", "esip-contract.js")] == 1
        assert requests["/static/esip-contract.js"] == 1
        assert not errors
    finally:
        context.close()
        server.shutdown()
        server.server_close()
        thread.join()

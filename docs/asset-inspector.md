# Asset inspector

MC2's inspector displays content associated with an ordinary geographic asset.
The application supplies the content; MC2 owns selection, geographic anchoring,
presentation, and dismissal. No station or analysis-specific asset type is needed.

Opt an asset in through `metadata.extra.inspector`:

```json
{
  "title": "Observation site",
  "description": "Measurements collected at this location.",
  "extra": {
    "inspector": {
      "version": 1,
      "status": "ready",
      "attachments": [
        {
          "type": "html",
          "title": "Observation history",
          "url": "https://artifacts.example.org/history.html"
        }
      ]
    }
  }
}
```

`version` defaults to `1`; unknown versions are rejected. Optional `status` is one of `loading`, `ready`,
`empty`, or `error`. `attachments` defaults to an empty list. Each attachment
has a `type` (`html`, `image`, or `link`), a `url`, and an optional `title`.
URLs must use HTTP(S) or be relative to the MC2 map page, and must be
free of credentials, whitespace, control characters, and backslashes. Content must be
accessible to the viewer's browser. Applications remain responsible for artifact
hosting, access, link lifetimes, and refreshing expired references.

Titles and descriptions are plain text. HTML content is a URL reference and
is rendered in a sandboxed iframe; supplied markup is never inserted into MC2's
DOM. There is no additional `inspectable` flag. Setting the inspector to `null`
disables the inspector for that asset.

## Updating content after asset creation

```python
session.update_metadata(asset_id, {
    "description": "The updated interpretation.",
    "extra": {
        "inspector": {
            "version": 1,
            "status": "ready",
            "attachments": [{"type": "html", "url": chart_url}],
        },
    },
})
```

`MapSession.update_metadata(asset_id, Metadata | dict)` sends an `update_metadata`
event. The event persists metadata and broadcasts the complete merged value as
`data: {asset_id, metadata}`. An already-open inspector refreshes without changing
the selected asset. Reconnecting viewers receive the same metadata in their
session snapshot.

Both this event and `PATCH /api/maps/{map_id}/assets/{asset_id}` merge supplied
`title` and `description` fields and shallow-merge keys inside `extra`. Other
metadata is preserved. Each supplied `extra` namespace, including `inspector`,
is replaced as a whole; send the complete new inspector descriptor when changing
its state or attachments. Use a dict with an explicit `null` to clear a text
field or set `extra.inspector` to `null` to disable inspection. An empty `extra`
object does not clear existing namespaces.

The REST PATCH endpoint broadcasts `asset_updated` with the complete asset model
in `data`, including the merged metadata. Invalid inspector descriptors or unsafe
URL schemes are rejected. `update_metadata` uses the event API's `error` response
field for missing assets and invalid data; the SDK raises `ServerError` for these.

## Viewer behavior and embedding

The inspector is enabled with `ui=default`. Embedders using `ui=controls` or
`ui=none` opt in with `inspect=1`; `inspect=0` disables it in every UI mode.
For example, `/map/{map_id}?ui=controls&inspect=1` keeps the native navigation and
drawing controls while allowing inspection without the built-in layer panel.

Clicking an enabled asset opens one MapLibre popup. Points anchor at their exact
geometry; other geometries anchor at the clicked location. The popup follows
map movements, and Expand gives a larger view within the map. Small map frames
use a bottom sheet. Clicking another enabled asset replaces the content;
background clicks, ordinary asset clicks, Close, and Escape dismiss it.
Drawing/deleting mode suppresses inspection. Hidden or deleted selections close.
Selection and expansion are local to the viewer and never sent as map events.

The ordinary `asset_click` event includes `inspector_handled: true` when MC2
opened the inspector, and `false` otherwise. An embedding app should suppress its
own selection card/layer drawer when this is true. The event also includes the
rendered `feature_id` when available; generated feature IDs are local to the
current asset geometry and should not be used as durable artifact identities.

HTML attachments execute only in an `allow-scripts` iframe sandbox, without
same-origin access to MC2. For CORS-readable documents, MC2 adds a sandboxed
Escape-key bridge; documents that cannot be fetched through CORS load directly
in the same sandbox. In that fallback, Close remains available outside the
frame and Escape works when focus is on MC2. Attachment links open separately.

An embedder can close inspection when focus/click moves outside its map iframe:

```javascript
mapIframe.contentWindow.postMessage({
  source: 'esip-embedder',
  type: 'close_inspector',
  map_id: mapId,
}, new URL(mapIframe.src).origin);
```

MC2 accepts that message only from its parent window with a matching map ID and,
when supplied by the browser, a matching referrer origin. Same-document consumers
can call `window.ESIPMap.inspector?.close()`.

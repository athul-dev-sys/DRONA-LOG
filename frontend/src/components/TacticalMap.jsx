import { Map as maplibreglMap, Marker, NavigationControl, AttributionControl, Popup as maplibreglPopup } from 'maplibre-gl'
import { useEffect, useRef } from 'react'

// Dark tactical style. The satellite basemap is NOT part of this style: it is
// added after 'load' (see addBasemap) so a slow or failed image fetch can never
// block the load event and blank out the operational layers.
// Assets are locally vendored (tools/build_basemap.py) — zero runtime network calls.
const STYLE = {
  version: 8,
  sources: {
    grid: { type: 'geojson', data: gridGeo() },
  },
  layers: [
    { id: 'bg', type: 'background', paint: { 'background-color': '#D6CCB4' } },
    {
      id: 'grid',
      type: 'line',
      source: 'grid',
      paint: { 'line-color': '#E8DFC9', 'line-width': 1, 'line-opacity': 0.22 },
    },
  ],
}

// Corner coords from the .json sidecars written by tools/build_basemap.py.
const BASEMAP_LAYERS = [
  {
    source: {
      sat: {
        type: 'image',
        url: '/satellite/ladakh_sat.jpg',
        coordinates: [[72.7734375, 37.16031654673675], [82.265625, 37.16031654673675],
                      [82.265625, 30.751277776257805], [72.7734375, 30.751277776257805]],
      },
    },
    layer: {
      id: 'sat',
      type: 'raster',
      source: 'sat',
      paint: { 'raster-saturation': -0.35, 'raster-brightness-max': 0.85 },
    },
  },
  {
    source: {
      hill: {
        type: 'image',
        url: '/satellite/ladakh_hillshade.jpg',
        coordinates: [[72.421875, 37.16031654673675], [82.265625, 37.16031654673675],
                      [82.265625, 30.751277776257805], [72.421875, 30.751277776257805]],
      },
    },
    layer: {
      id: 'hill',
      type: 'raster',
      source: 'hill',
      paint: { 'raster-opacity': 0.3 },
    },
  },
]

// Adds the satellite imagery + hillshade just above the background, below every
// operational layer. Non-fatal: if either image fails, the tactical map still
// renders on the plain dark style.
function addBasemap(map) {
  if (map.getSource('sat')) return
  try {
    for (const { source, layer } of BASEMAP_LAYERS) {
      const id = Object.keys(source)[0]
      map.addSource(id, source[id])
      map.addLayer(layer, 'grid')
    }
  } catch (err) {
    console.error('basemap unavailable, continuing without imagery:', err)
  }
}

function gridGeo() {
  const feats = []
  for (let lon = 74; lon <= 81; lon += 1) {
    feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: [[lon, 31.5], [lon, 36]] } })
  }
  for (let lat = 31.5; lat <= 36; lat += 1) {
    feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: [[74, lat], [81, lat]] } })
  }
  return { type: 'FeatureCollection', features: feats }
}

const NODE_STYLE = {
  gateway: { color: '#8C7440', size: 6, label: 'GATEWAY' },
  depot: { color: '#3F6B3F', size: 10, label: 'BASE DEPOT' },
  staging: { color: '#2F6F77', size: 8, label: 'STAGING' },
  fob: { color: '#C08A2E', size: 9, label: 'FOB' },
  pass: { color: '#EFE7D6', size: 6, label: 'PASS' },
  transit: { color: '#B9AD94', size: 4, label: '' },
}

const WEST_PASSES = new Set(['ZOJILA', 'NAMIKALA', 'FOTULA'])
const SOUTH_PASSES = new Set(['ROHTANG', 'BARALACHA', 'TANGLANGLA'])

function corridorShut(closedPasses, key) {
  const closed = new Set(Object.keys(closedPasses || {}))
  const set = key === 'west' ? WEST_PASSES : SOUTH_PASSES
  return [...closed].some((p) => set.has(p)) ? 1 : 0
}

export default function TacticalMap({ network, plan, closure, onFobClick }) {
  const ref = useRef(null)
  const mapRef = useRef(null)
  const clickRef = useRef(onFobClick)
  clickRef.current = onFobClick

  useEffect(() => {
    const map = new maplibreglMap({
      container: ref.current,
      style: STYLE,
      center: [77.4, 34.0],
      zoom: 7,
      attributionControl: false,
    })
    mapRef.current = map
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    map.addControl(new AttributionControl({
      compact: true,
      customAttribution: 'Imagery: Esri, Maxar, Earthstar Geographics · Terrain: Mapzen/AWS (SRTM)',
    }), 'bottom-right')
    map.on('error', (e) => console.error('maplibre error:', e?.error?.message || e))
    if (map.isStyleLoaded()) addBasemap(map)
    else map.once('load', () => addBasemap(map))
    return () => { map.remove(); mapRef.current = null }
  }, [])

  // ---- static network layers (once map ready + network loaded) ----
  useEffect(() => {
    const map = mapRef.current
    if (!map || !network) return
    const add = () => {
      if (map.getSource('edges')) return
      // road edges
      const nodeById = Object.fromEntries(network.nodes.map((n) => [n.id, n]))
      const edgeFeats = network.edges.map((e) => ({
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: [
            [nodeById[e.u].lon, nodeById[e.u].lat],
            [nodeById[e.v].lon, nodeById[e.v].lat],
          ],
        },
        properties: { corridor: e.corridor, closed: 0 },
      }))
      map.addSource('edges', { type: 'geojson', data: { type: 'FeatureCollection', features: edgeFeats } })
      map.addLayer({
        id: 'edges-line',
        type: 'line',
        source: 'edges',
        paint: { 'line-color': '#F1E7D0', 'line-width': 1.5, 'line-opacity': 0.4 },
      })

      // gateway corridor routes (west/south) - coloured by open/closed
      const gwFeats = []
      const addGw = (key, r) => {
        if (!r) return
        gwFeats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: r.geometry.map((g) => [g[1], g[0]]) }, properties: { which: key, closed: corridorShut(network.closed_passes, key) } })
      }
      addGw('west', network.gateway_routes?.west || plan?.gateway_routes?.west)
      addGw('south', network.gateway_routes?.south || plan?.gateway_routes?.south)
      map.addSource('gw', { type: 'geojson', data: { type: 'FeatureCollection', features: gwFeats } })
      map.addLayer({
        id: 'gw-line',
        type: 'line',
        source: 'gw',
        paint: {
          'line-color': ['case', ['==', ['get', 'closed'], 1], '#C0392B',
            ['match', ['get', 'which'], 'west', '#2F7D3A', '#2F6F77']],
          'line-width': ['case', ['==', ['get', 'closed'], 1], 3, 2.5],
          'line-opacity': 0.9,
        },
      })

      // pass nodes as squares
      const passFeats = network.nodes.filter((n) => n.kind === 'pass').map((n) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [n.lon, n.lat] },
        properties: { id: n.id, name: n.name, closed: 0 },
      }))
      map.addSource('passes', { type: 'geojson', data: { type: 'FeatureCollection', features: passFeats } })
      map.addLayer({
        id: 'pass-fill',
        type: 'circle',
        source: 'passes',
        paint: {
          'circle-color': ['case', ['==', ['get', 'closed'], 1], '#C0392B', '#EFE7D6'],
          'circle-radius': 5,
          'circle-stroke-color': '#3B4A2F',
          'circle-stroke-width': 1,
        },
      })

      // depot/staging/fob nodes
      const nodeFeats = network.nodes
        .filter((n) => ['depot', 'staging', 'fob', 'gateway'].includes(n.kind))
        .map((n) => ({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [n.lon, n.lat] },
          properties: { id: n.id, name: n.name, kind: n.kind },
        }))
      map.addSource('nodes', { type: 'geojson', data: { type: 'FeatureCollection', features: nodeFeats } })
      map.addLayer({
        id: 'node-fill',
        type: 'circle',
        source: 'nodes',
        paint: {
          'circle-color': ['match', ['get', 'kind'], 'depot', '#3F6B3F', 'staging', '#2F6F77', 'fob', '#C08A2E', '#8C7440'],
          'circle-radius': ['match', ['get', 'kind'], 'depot', 9, 'staging', 7, 'fob', 7.5, 5],
          'circle-stroke-color': '#F7F3E8',
          'circle-stroke-width': 1.5,
        },
      })
      // HTML labels: no glyphs source required (fully offline basemap).
      network.nodes
        .filter((n) => ['depot', 'staging', 'fob', 'gateway'].includes(n.kind))
        .forEach((n) => {
          const el = document.createElement('div')
          el.className = 'map-node-label'
          el.textContent = n.id
          new Marker({ element: el, anchor: 'top', offset: [0, 7] })
            .setLngLat([n.lon, n.lat])
            .addTo(map)
        })
      map.on('click', 'node-fill', (ev) => {
        const p = ev.features[0].properties
        if (p.kind === 'fob') clickRef.current?.(p.id)
      })
      map.on('mouseenter', 'node-fill', () => (map.getCanvas().style.cursor = 'pointer'))
      map.on('mouseleave', 'node-fill', () => (map.getCanvas().style.cursor = ''))

      const focus = network.nodes.filter((n) => ['depot', 'staging', 'fob'].includes(n.kind))
      const lons = focus.map((n) => n.lon)
      const lats = focus.map((n) => n.lat)
      map.fitBounds(
        [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
        { padding: 46, duration: 0 },
      )

      // hover popup for passes
      const popup = new maplibreglPopup({ closeButton: false, offset: 8 })
      map.on('mouseenter', 'pass-fill', (ev) => {
        const p = ev.features[0].properties
        popup.setLngLat(ev.lngLat).setHTML(`<b>${p.name}</b><br>${p.closed ? 'CLOSED' : 'open'} · ${p.id}`).addTo(map)
      })
      map.on('mouseleave', 'pass-fill', () => popup.remove())
    }
    if (map.loaded() || map.isStyleLoaded()) add()
    else map.on('load', add)
  }, [network])

  // ---- reflect pass closures ----
  useEffect(() => {
    const map = mapRef.current
    if (!map || !network || !map.getSource('passes')) return
    const closedPasses = { ...(network.closed_passes || {}) }
    if (closure) closedPasses[closure] = true
    const data = {
      type: 'FeatureCollection',
      features: network.nodes.filter((n) => n.kind === 'pass').map((n) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [n.lon, n.lat] },
        properties: { id: n.id, name: n.name, closed: closedPasses[n.id] ? 1 : 0 },
      })),
    }
    map.getSource('passes').setData(data)
    // corridor colour: red when a pass on that corridor is closed
    const gwFeats = []
    ;['west', 'south'].forEach((key) => {
      const r = network.gateway_routes?.[key]
      if (!r) return
      gwFeats.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: r.geometry.map((g) => [g[1], g[0]]) },
        properties: { which: key, closed: corridorShut(closedPasses, key) },
      })
    })
    if (map.getSource('gw')) {
      map.getSource('gw').setData({ type: 'FeatureCollection', features: gwFeats })
    }
  }, [closure, network])

  // ---- plan routes (delivery lines) ----
  useEffect(() => {
    const map = mapRef.current
    if (!map || !plan) return
    const update = () => {
      if (!map.getSource('edges')) return
      const feats = []
      Object.values(plan.routes || {}).forEach((r) => {
        if (!r) return
        feats.push({
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: r.geometry.map((g) => [g[1], g[0]]) },
          properties: { distance: r.distance_km },
        })
      })
      if (!map.getSource('plan-routes')) {
        map.addSource('plan-routes', { type: 'geojson', data: { type: 'FeatureCollection', features: feats } })
        map.addLayer({
          id: 'plan-line',
          type: 'line',
          source: 'plan-routes',
          paint: { 'line-color': '#E8892B', 'line-width': 2.5, 'line-dasharray': [2, 1.5] },
        })
      } else {
        map.getSource('plan-routes').setData({ type: 'FeatureCollection', features: feats })
      }
    }
    if (map.getSource('edges')) update()
    else map.on('load', update)
    return () => { map.off('load', update) }
  }, [plan])

  return <div ref={ref} className="h-full w-full" />
}

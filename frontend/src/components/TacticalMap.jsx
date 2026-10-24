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

export default function TacticalMap({ network, plan, closure, choreography, onFobClick }) {
  const ref = useRef(null)
  const mapRef = useRef(null)
  const clickRef = useRef(onFobClick)
  const startRef = useRef(0)
  const homeRef = useRef(null)       // saved full-theatre bounds for choreography return
  const choreoRef = useRef(0)        // token: bumps cancel any running choreography
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
      homeRef.current = {
        bounds: [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
      }
      map.fitBounds(homeRef.current.bounds, { padding: 46, duration: 0 })

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

  // ---- closure camera choreography ----
  // Beat 1: fly to the closed pass. Beat 2: pass flashes red. Beat 3: west
  // corridor pulses red. Beat 4: fly along the alternate (south) route with a
  // flowing dash. Beat 5: settle back to the full-theatre view.
  // Any user drag (movestart from a gesture) or a new choreography cancels it;
  // paint properties we pulse are restored on cancel.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !choreography) return
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches) return

    const token = ++choreoRef.current
    const choreo = choreoRef              // stable local handle for cleanup/rAF
    const myJob = () => choreo.current === token

    const { lat, lon, route } = choreography   // pass coords from the event payload
    if (lat == null || lon == null || !route?.length) return

    const timers = []
    const at = (ms, fn) => timers.push(setTimeout(() => { if (myJob()) fn() }, ms))
    let raf = 0

    // snapshot the paint props we animate so cancel/finish can restore them
    const origPassRadius = map.getPaintProperty('pass-fill', 'circle-radius')
    const origPassStroke = map.getPaintProperty('pass-fill', 'circle-stroke-width')
    const origGwWidth = map.getPaintProperty('gw-line', 'line-width')
    const origGwOpacity = map.getPaintProperty('gw-line', 'line-opacity')

    const restore = () => {
      cancelAnimationFrame(raf)
      // map may already be removed (unmount cleanup order) — guard everything
      if (!mapRef.current) return
      try {
        if (map.getLayer('pass-fill')) {
          map.setPaintProperty('pass-fill', 'circle-radius', origPassRadius)
          map.setPaintProperty('pass-fill', 'circle-stroke-width', origPassStroke)
        }
        if (map.getLayer('gw-line')) {
          map.setPaintProperty('gw-line', 'line-width', origGwWidth)
          map.setPaintProperty('gw-line', 'line-opacity', origGwOpacity)
        }
        if (map.getSource('choreo-flow')) {
          map.removeLayer('choreo-flow')
          map.removeSource('choreo-flow')
        }
      } catch { /* style tearing down */ }
    }

    const cancel = () => {
      if (!myJob()) return
      choreo.current++
      timers.forEach(clearTimeout)
      restore()
      map.off('movestart', onGesture)
    }
    // a programmatic easeTo/flyTo does not fire drag events, but user
    // interaction resets choreography — check who initiated via stopAlgo flag
    let programmatic = false
    const onGesture = () => { if (!programmatic) cancel() }

    const goto = (opts) => {
      programmatic = true
      // easeTo has no bounds option — resolve bounds to center/zoom first,
      // otherwise the camera silently stays put and the beat does nothing.
      const { bounds, padding, ...rest } = opts
      const target = bounds ? map.cameraForBounds(bounds, { padding: padding || 46 }) : rest
      map.easeTo({ ...rest, ...target, essential: true, easing: (t) => t * t * (3 - 2 * t) })
      // easeTo ends asynchronously; reopen the gesture gate right after
      at(opts.duration + 50, () => { programmatic = false })
    }

    map.on('movestart', onGesture)

    // ---- Beat 1 (0s): fly to the pass ----
    const PASS_ZOOM = 9.5
    goto({ center: [lon, lat], zoom: PASS_ZOOM, duration: 1200 })

    // ---- Beat 2 (1.2s): pass flashes red 3x ----
    at(1200, () => {
      const t0 = performance.now()
      const FLASH_MS = 900
      const pulsePass = (now) => {
        if (!myJob()) return
        const t = now - t0
        if (t >= FLASH_MS) {
          // leave the pass closed-red (static state comes from the closure
          // effect); just stop pulsing
          return
        }
        const on = Math.floor(t / 150) % 2 === 0
        map.setPaintProperty('pass-fill', 'circle-radius', on ? 9 : 5)
        map.setPaintProperty('pass-fill', 'circle-stroke-width', on ? 3 : 1)
        raf = requestAnimationFrame(pulsePass)
      }
      raf = requestAnimationFrame(pulsePass)
    })

    // ---- Beat 3 (2.1s): west corridor pulses red ----
    at(2100, () => {
      const t0 = performance.now()
      const PULSE_MS = 700
      const pulseGw = (now) => {
        if (!myJob()) return
        const t = now - t0
        if (t >= PULSE_MS) return
        const phase = (Math.sin((t / PULSE_MS) * Math.PI * 3) + 1) / 2
        map.setPaintProperty('gw-line', 'line-width', 3 + phase * 3)
        map.setPaintProperty('gw-line', 'line-opacity', 0.7 + phase * 0.3)
        raf = requestAnimationFrame(pulseGw)
      }
      raf = requestAnimationFrame(pulseGw)
    })

    // ---- Beat 4 (2.5s): fly along the alternate route with flowing dash ----
    at(2500, () => {
      const coords = route.map((g) => [g[1], g[0]])    // [lat,lon] -> [lon,lat]
      // overlay a bright flowing line on the alternate corridor
      if (!map.getSource('choreo-flow')) {
        map.addSource('choreo-flow', {
          type: 'geojson',
          data: { type: 'Feature', geometry: { type: 'LineString', coordinates: coords },
                  properties: {} },
        })
        map.addLayer({
          id: 'choreo-flow',
          type: 'line',
          source: 'choreo-flow',
          paint: {
            'line-color': '#22d3ee',
            'line-width': 4,
            'line-dasharray': [0.6, 0.6],
            'line-opacity': 0.95,
          },
        }, 'gw-line')   // just above the corridor layer
      }
      // dash-flow: maplibre has no dash offset, so fake forward motion by
      // cycling dash opacity — reads as "supply flowing down this corridor"
      const t0 = performance.now()
      const flow = (now) => {
        if (!myJob()) return
        const k = (Math.sin((now - t0) / 180) + 1) / 2
        map.setPaintProperty('choreo-flow', 'line-opacity', 0.65 + k * 0.35)
        raf = requestAnimationFrame(flow)
      }
      raf = requestAnimationFrame(flow)

      // camera: ease to route midpoint at medium zoom, then to route bounds
      const mid = coords[Math.floor(coords.length / 2)]
      const lons = coords.map((c) => c[0])
      const lats = coords.map((c) => c[1])
      programmatic = true
      map.easeTo({
        center: mid, zoom: 7.6, duration: 900,
        essential: true, easing: (t) => t * t * (3 - 2 * t),
      })
      at(950, () => {
        goto({
          bounds: [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
          padding: 70, duration: 800,
        })
      })
    })

    // ---- Beat 5 (~4.5s): settle home, clean up ----
    at(4700, () => {
      goto({ ...homeRef.current, padding: 46, duration: 600, pitch: 0, bearing: 0 })
      at(700, () => {
        if (!myJob()) return
        timers.forEach(clearTimeout)
        restore()
        map.off('movestart', onGesture)
      })
    })

    return () => {
      choreo.current++           // unmount/new-choreography cancels everything
      timers.forEach(clearTimeout)
      restore()
      map.off('movestart', onGesture)
    }
  }, [choreography])

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

  // ---- convoy animation: one moving dot per planned dispatch ----
  // The 7-day horizon is compressed into a CYCLE loop: a convoy departs at the
  // start of its plan-day, travels its route for TRAVEL_MS, then is delivered
  // (hidden) until the cycle restarts.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !plan) return

    const convoys = []
    for (const dl of plan.deliveries || []) {
      const r = plan.routes?.[`${dl.from}|${dl.to}`]
      if (!r?.geometry?.length) continue
      const pts = r.geometry.map((g) => [g[1], g[0]]) // [lon, lat]
      const cum = [0]
      for (let i = 1; i < pts.length; i++) {
        // scale lon by cos(lat) so distances are roughly isotropic
        const k = Math.cos((pts[i][1] + pts[i - 1][1]) / 2 * Math.PI / 180)
        cum.push(cum[i - 1] + Math.hypot((pts[i][0] - pts[i - 1][0]) * k, pts[i][1] - pts[i - 1][1]))
      }
      if (cum[cum.length - 1] > 0) {
        convoys.push({ day: dl.day, trucks: dl.trucks, pts, cum, total: cum[cum.length - 1] })
      }
    }

    const DAY_MS = 3200
    const TRAVEL_MS = 2600
    const CYCLE_MS = 7 * DAY_MS
    const EMPTY = { type: 'FeatureCollection', features: [] }

    const ensureSource = () => {
      if (map.getSource('convoys')) return
      map.addSource('convoys', { type: 'geojson', data: EMPTY })
      map.addLayer({
        id: 'convoy-halo',
        type: 'circle',
        source: 'convoys',
        paint: {
          'circle-color': '#f59e0b',
          'circle-radius': ['+', 6, ['*', 2.5, ['min', ['get', 'trucks'], 4]]],
          'circle-blur': 1,
          'circle-opacity': 0.35,
        },
      })
      map.addLayer({
        id: 'convoy-dot',
        type: 'circle',
        source: 'convoys',
        paint: {
          'circle-color': '#fbbf24',
          'circle-radius': ['+', 3.5, ['*', 1.2, ['min', ['get', 'trucks'], 4]]],
          'circle-stroke-color': '#0a0f14',
          'circle-stroke-width': 1.5,
        },
      })
    }

    // smoothstep easing so trucks accelerate out of the depot and ease in
    const ease = (p) => p * p * (3 - 2 * p)

    const pointAt = (c, frac) => {
      const target = ease(frac) * c.total
      let i = 1
      while (i < c.cum.length - 1 && c.cum[i] < target) i++
      const seg = c.cum[i] - c.cum[i - 1] || 1
      const f = (target - c.cum[i - 1]) / seg
      return [
        c.pts[i - 1][0] + (c.pts[i][0] - c.pts[i - 1][0]) * f,
        c.pts[i - 1][1] + (c.pts[i][1] - c.pts[i - 1][1]) * f,
      ]
    }

    let raf = 0
    const tick = (now) => {
      const t = (now - startRef.current) % CYCLE_MS
      const feats = []
      for (const c of convoys) {
        const t0 = c.day * DAY_MS
        if (t < t0 || t > t0 + TRAVEL_MS) continue
        const [lon, lat] = pointAt(c, (t - t0) / TRAVEL_MS)
        feats.push({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [lon, lat] },
          properties: { trucks: c.trucks },
        })
      }
      map.getSource('convoys')?.setData({ type: 'FeatureCollection', features: feats })
      raf = requestAnimationFrame(tick)
    }

    const begin = () => {
      ensureSource()
      cancelAnimationFrame(raf)
      startRef.current = performance.now()
      raf = requestAnimationFrame(tick)
    }
    if (map.isStyleLoaded()) begin()
    else map.once('load', begin)
    return () => cancelAnimationFrame(raf)
  }, [plan])

  return <div ref={ref} className="h-full w-full" />
}

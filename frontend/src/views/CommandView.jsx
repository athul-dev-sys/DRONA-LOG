import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import TacticalMap from '../components/TacticalMap.jsx'
import { api, fmtINR, fmtNum } from '../api.js'

const CLASS_META = { I: { name: 'I · Rations', unit: 't' }, III: { name: 'III · POL', unit: 'kl' }, V: { name: 'V · Ammo', unit: 't' } }

function Bar({ pct, color }) {
  return (
    <div className="h-1.5 w-full rounded bg-[#1d2836]">
      <div className="h-1.5 rounded" style={{ width: `${Math.min(100, Math.max(2, pct))}%`, background: color }} />
    </div>
  )
}

function signedReduction(pct) {
  const v = Number(pct) || 0
  return `${v >= 0 ? '−' : '+'}${fmtNum(Math.abs(v), 1)}%`
}

function Metric({ label, value, sub, tone = 'good' }) {
  const tones = {
    good: 'text-emerald-400 border-emerald-900/60',
    warn: 'text-amber-400 border-amber-900/60',
    bad: 'text-rose-400 border-rose-900/60',
    plain: 'text-slate-300 border-slate-800',
  }
  return (
    <div className={`rounded border bg-[#0d141c] px-3 py-2 ${tones[tone]}`}>
      <div className="text-[10px] uppercase tracking-widest text-slate-500">{label}</div>
      <div className="mt-0.5 text-lg font-semibold leading-tight">{value}</div>
      {sub && <div className="text-[10px] text-slate-500">{sub}</div>}
    </div>
  )
}

export default function CommandView() {
  const [network, setNetwork] = useState(null)
  const [plan, setPlan] = useState(null)
  const [metrics, setMetrics] = useState(null)
  const [closure, setClosure] = useState(null)      // active pass-closure response
  const [closedPass, setClosedPass] = useState(null)
  const [busy, setBusy] = useState(false)
  const [selFob, setSelFob] = useState(null)
  const [forecast, setForecast] = useState(null)
  const [err, setErr] = useState(null)

  const load = async () => {
    try {
      setErr(null)
      const [n, p, m, f] = await Promise.all([
        api('/api/network'), api('/api/plan'), api('/api/metrics'), api('/api/forecast?days=7'),
      ])
      setNetwork(n); setPlan(p); setMetrics(m); setForecast(f)
      const closed = Object.keys(n.closed_passes || {})
      setClosedPass(closed[0] || null)
    } catch (e) { setErr(e.message) }
  }
  useEffect(() => { load() }, [])

  const fobStock = useMemo(() => {
    if (!network) return {}
    const out = {}
    network.stock.forEach((s) => {
      if (!out[s.node_id]) out[s.node_id] = {}
      out[s.node_id][s.class_id] = s
    })
    return out
  }, [network])

  const fobNames = useMemo(() => {
    if (!network) return {}
    return Object.fromEntries(network.nodes.map((n) => [n.id, n.name]))
  }, [network])

  const closePass = async (passId, action) => {
    setBusy(true); setErr(null)
    try {
      const r = await api('/api/events/pass-closure', {
        method: 'POST',
        body: JSON.stringify({ pass_id: passId, action, days: 7 }),
      })
      setClosure(r)
      setClosedPass(action === 'closed' ? passId : null)
      setPlan(r.new_plan)
      const n = await api('/api/network')
      setNetwork(n)
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }

  const selStock = selFob ? fobStock[selFob] : null
  const selForecast = forecast?.fobs?.[selFob]

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-[#1b2735] bg-[#0c1219] px-4 py-2">
        <div>
          <div className="text-sm font-semibold tracking-widest text-slate-200">DRONA-LOG · SIH26251</div>
          <div className="text-[10px] uppercase tracking-widest text-slate-500">
            Predictive logistics &amp; forward supply chain · Ladakh sector
          </div>
        </div>
        <div className="flex items-center gap-4 text-[11px]">
          {plan && (
            <span className="text-slate-400">
              SOLVE <span className="text-emerald-400">{plan.status}</span> · {fmtNum(plan.solve_time_ms, 0)} ms
            </span>
          )}
          <span className="rounded border border-amber-800/60 bg-amber-950/30 px-2 py-0.5 text-amber-400">SIMULATED DATA</span>
          <button onClick={load} className="rounded border border-slate-700 px-2 py-0.5 text-slate-300 hover:bg-[#16202c]">REFRESH</button>
          <Link to="/edge" className="rounded border border-cyan-800/60 bg-cyan-950/30 px-2 py-0.5 text-cyan-300">EDGE CLIENT →</Link>
        </div>
      </header>

      {err && (
        <div className="border-b border-rose-900/50 bg-rose-950/40 px-4 py-1 text-[11px] text-rose-300">
          {err}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* left: map */}
        <div className="relative min-w-0 flex-1">
          {network && <TacticalMap network={network} plan={plan} closure={closedPass} onFobClick={setSelFob} />}
          {/* pass closure controls */}
          <div className="absolute left-3 top-3 rounded border border-[#233140] bg-[#0d141ccc] p-3 backdrop-blur">
            <div className="mb-2 text-[10px] uppercase tracking-widest text-slate-500">Pass closure simulation</div>
            <div className="flex flex-col gap-1.5">
              <button
                disabled={busy || closedPass === 'ZOJILA'}
                onClick={() => closePass('ZOJILA', 'closed')}
                className="rounded border border-rose-800/70 bg-rose-950/40 px-3 py-1 text-[11px] text-rose-300 hover:bg-rose-950/70 disabled:opacity-40"
              >
                CLOSE ZOJI LA (west corridor)
              </button>
              <button
                disabled={busy || closedPass !== 'ZOJILA'}
                onClick={() => closePass('ZOJILA', 'reopened')}
                className="rounded border border-emerald-800/70 bg-emerald-950/40 px-3 py-1 text-[11px] text-emerald-300 hover:bg-emerald-950/70 disabled:opacity-40"
              >
                REOPEN ZOJI LA
              </button>
              {busy && <div className="text-[11px] text-amber-400">rerouting + re-solving…</div>}
            </div>
            {closedPass && (
              <div className="mt-2 border-t border-[#233140] pt-2 text-[11px] text-rose-400">
                ZOJI LA CLOSED — west corridor severed. Inflow via Manali (south).
              </div>
            )}
          </div>
          <div className="absolute bottom-3 left-3 rounded border border-[#233140] bg-[#0d141ccc] px-3 py-2 text-[10px] text-slate-400 backdrop-blur">
            <div className="mb-1 flex items-center gap-3">
              <span><span className="inline-block h-2 w-2 rounded-full bg-emerald-400" /> base depot</span>
              <span><span className="inline-block h-2 w-2 rounded-full bg-cyan-400" /> staging</span>
              <span><span className="inline-block h-2 w-2 rounded-full bg-amber-400" /> FOB</span>
              <span><span className="inline-block h-2 w-2 rounded-full bg-rose-500" /> closed pass</span>
            </div>
            <div>corridor: <span className="text-emerald-400">green</span> open · <span className="text-rose-400">red</span> closed · <span className="text-cyan-400">cyan</span> Manali · dashed amber = planned convoys</div>
          </div>
        </div>

        {/* right: panels */}
        <aside className="flex w-[420px] flex-col overflow-y-auto border-l border-[#1b2735] bg-[#0b1118]">
          {/* closure diff */}
          {closure && (
            <section className="border-b border-[#1b2735] p-3">
              <div className="mb-2 flex items-center justify-between">
                <div className="text-[11px] uppercase tracking-widest text-rose-400">Pass closure impact — {closure.pass.id}</div>
                <button onClick={() => setClosure(null)} className="text-slate-500 hover:text-slate-300">✕</button>
              </div>
              <div className="grid grid-cols-2 gap-2 text-[11px]">
                <div className="rounded border border-[#233140] p-2">
                  <div className="text-slate-500">Reroute time</div>
                  <div className="text-base text-amber-300">{fmtNum(closure.router_time_ms, 1)} ms</div>
                </div>
                <div className="rounded border border-[#233140] p-2">
                  <div className="text-slate-500">Re-solve time</div>
                  <div className="text-base text-amber-300">{fmtNum(closure.solve_time_ms, 1)} ms</div>
                </div>
              </div>
              <div className="mt-2 grid grid-cols-3 gap-2 text-center text-[11px]">
                <div className="rounded border border-rose-900/50 bg-rose-950/20 p-1.5">
                  <div className="text-slate-500">Cancelled</div>
                  <div className="text-rose-300">{closure.diff.counts.removed}</div>
                </div>
                <div className="rounded border border-emerald-900/50 bg-emerald-950/20 p-1.5">
                  <div className="text-slate-500">Added</div>
                  <div className="text-emerald-300">{closure.diff.counts.added}</div>
                </div>
                <div className="rounded border border-amber-900/50 bg-amber-950/20 p-1.5">
                  <div className="text-slate-500">Changed</div>
                  <div className="text-amber-300">{closure.diff.counts.changed}</div>
                </div>
              </div>
              {/* inventory dip: LEH stock projection old vs new */}
              <div className="mt-3 text-[11px]">
                <div className="mb-1 text-slate-500">LEH depot projection (class I) — before vs after</div>
                <DipChart
                  before={closure.old_plan?.depot_projection?.LEH?.I}
                  after={closure.new_plan?.depot_projection?.LEH?.I}
                />
                <div className="mt-1 text-slate-500">
                  Alternate gateway: Manali → Leh, {fmtNum(closure.gateway_routes?.south?.distance_km, 0)} km, cost-km {fmtNum(closure.gateway_routes?.south?.cost_km, 0)}
                </div>
              </div>
            </section>
          )}

          {/* metrics */}
          {metrics?.delta && (
            <section className="border-b border-[#1b2735] p-3">
              <div className="mb-2 flex items-center justify-between">
                <div className="text-[11px] uppercase tracking-widest text-slate-400">Impact vs naive baseline</div>
                <span className="text-[9px] text-amber-500">30-day sim · {metrics.label}</span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Metric label="Stockouts" value={signedReduction(metrics.delta.stockout_reduction_pct)} sub={`${fmtNum(metrics.naive.stockout_units, 1)} → ${fmtNum(metrics.drona.stockout_units, 1)} units`} tone={metrics.delta.stockout_reduction_pct >= 0 ? 'good' : 'bad'} />
                <Metric label="Requisitions held" value={signedReduction(metrics.delta.hold_event_reduction_pct)} sub={`${metrics.naive.hold_events} → ${metrics.drona.hold_events} events`} tone={metrics.delta.hold_event_reduction_pct >= 0 ? 'good' : 'bad'} />
                <Metric label="Cost / unit demand" value={signedReduction(metrics.delta.cost_change_pct)} sub={`${fmtINR(metrics.delta.cost_per_unit_naive_inr)} → ${fmtINR(metrics.delta.cost_per_unit_drona_inr)}`} tone={metrics.delta.cost_change_pct >= 0 ? 'good' : 'warn'} />
                <Metric
                  label="Fuel / unit demand"
                  value={`${-metrics.delta.fuel_change_pct <= 0 ? '−' : '+'}${fmtNum(Math.abs(metrics.delta.fuel_change_pct), 1)}%`}
                  sub={`${fmtNum(metrics.delta.fuel_per_unit_naive_km, 0)} → ${fmtNum(metrics.delta.fuel_per_unit_drona_km, 0)} km/unit — ${-metrics.delta.fuel_change_pct <= 0 ? 'lower: consolidated loads' : 'higher: south-corridor reroute during closure'}`}
                  tone={-metrics.delta.fuel_change_pct <= 0 ? 'good' : 'warn'}
                />
              </div>
              <div className="mt-2 text-[10px] leading-relaxed text-slate-500">
                Naive: nearest-depot resupply, 3-day fixed lead, no forecast/rerouting. DRONA: LightGBM + MILP, 1-day lead, corridor-aware. Includes Zoji La closure days {metrics.scenario.closure.from_day}–{metrics.scenario.closure.to_day}. All figures computed from the simulation.
              </div>
            </section>
          )}

          {/* plan */}
          {plan && (
            <section className="border-b border-[#1b2735] p-3">
              <div className="mb-2 flex items-baseline justify-between">
                <div className="text-[11px] uppercase tracking-widest text-slate-400">7-day allocation plan</div>
                <div className="text-[10px] text-slate-500">
                  {plan.status} · obj {fmtINR(plan.objective_inr)} · {plan.kpi.trucks} trucks
                </div>
              </div>
              <div className="max-h-56 overflow-y-auto text-[11px]">
                <table className="w-full">
                  <thead className="text-slate-500">
                    <tr><th className="text-left font-normal">D</th><th className="text-left font-normal">From → To</th><th className="text-left font-normal">Load (I / III / V)</th><th className="text-right font-normal">Cost</th></tr>
                  </thead>
                  <tbody>
                    {plan.deliveries.map((d, i) => (
                      <tr key={i} className="border-t border-[#141d28]">
                        <td className="py-0.5 text-slate-500">D{d.day}</td>
                        <td className="text-slate-300">{d.from} → {d.to}</td>
                        <td className="text-slate-400">{d.classes.I ?? '·'} / {d.classes.III ?? '·'} / {d.classes.V ?? '·'}</td>
                        <td className="text-right text-slate-400">{fmtINR(d.cost_inr)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-2 flex gap-3 text-[10px] text-slate-500">
                <span>stockouts: <span className="text-emerald-400">{fmtNum(plan.kpi.stockout_units, 2)} units</span></span>
                <span>transport: {fmtINR(plan.kpi.transport_inr)}</span>
                <span>solve: {fmtNum(plan.solve_time_ms, 0)} ms</span>
              </div>
            </section>
          )}

          {/* FOB stock list */}
          <section className="flex-1 p-3">
            <div className="mb-2 text-[11px] uppercase tracking-widest text-slate-400">FOB stock vs safety buffer</div>
            <div className="space-y-2">
              {network?.nodes.filter((n) => n.kind === 'fob').map((n) => {
                const st = fobStock[n.id] || {}
                const worst = Math.min(...['I', 'III', 'V'].map((c) => {
                  const s = st[c]
                  if (!s || !s.buffer) return 100
                  return (s.on_hand / s.buffer) * 100
                }))
                const tone = worst < 50 ? '#ef4444' : worst < 100 ? '#f59e0b' : '#4ade80'
                return (
                  <button
                    key={n.id}
                    onClick={() => setSelFob(selFob === n.id ? null : n.id)}
                    className={`block w-full rounded border px-2 py-1.5 text-left ${selFob === n.id ? 'border-amber-700 bg-amber-950/20' : 'border-[#1a2531] hover:bg-[#101923]'}`}
                  >
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="text-slate-300">{n.id} <span className="text-slate-600">{n.elev_m} m · {n.troops} pax</span></span>
                      <span style={{ color: tone }}>{fmtNum(worst, 0)}% of buffer</span>
                    </div>
                    <div className="mt-1"><Bar pct={worst} color={tone} /></div>
                    {selFob === n.id && selStock && (
                      <div className="mt-2 space-y-1 border-t border-[#1a2531] pt-2 text-[10px] text-slate-400">
                        {['I', 'III', 'V'].map((c) => {
                          const s = selStock[c]
                          const fc = selForecast?.forecast?.[c]
                          return (
                            <div key={c} className="flex justify-between">
                              <span>{CLASS_META[c].name}</span>
                              <span>
                                {fmtNum(s?.on_hand, 1)} / buffer {fmtNum(s?.buffer, 1)} {CLASS_META[c].unit}
                                {fc && <span className="text-slate-600"> · 7d fc {fmtNum(fc.total, 1)}</span>}
                              </span>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </button>
                )
              })}
            </div>
          </section>
        </aside>
      </div>
    </div>
  )
}

function DipChart({ before, after }) {
  if (!before || !after) return <div className="text-slate-600">no projection</div>
  const max = Math.max(...before, ...after) || 1
  const rows = before.map((b, i) => ({ b, a: after[i], i }))
  return (
    <div className="flex h-16 items-end gap-1">
      {rows.map((r) => (
        <div key={r.i} className="flex h-full flex-1 items-end gap-px">
          <div
            className="w-1/2 bg-slate-600"
            style={{ height: `${Math.max(3, (r.b / max) * 100)}%` }}
            title={`D${r.i}: ${fmtNum(r.b, 1)} before`}
          />
          <div
            className="w-1/2 bg-amber-500"
            style={{ height: `${Math.max(3, (r.a / max) * 100)}%` }}
            title={`D${r.i}: ${fmtNum(r.a, 1)} after`}
          />
        </div>
      ))}
    </div>
  )
}

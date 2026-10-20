import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import TacticalMap from '../components/TacticalMap.jsx'
import { api, fmtINR, fmtNum } from '../api.js'

const CLASS_META = { I: { name: 'I · Rations', unit: 't' }, III: { name: 'III · POL', unit: 'kl' }, V: { name: 'V · Ammo', unit: 't' } }

function Bar({ pct, color }) {
  return (
    <div className="h-1.5 w-full rounded bg-[#DDD5C2]">
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
    good: 'text-[#3F6B3F] border-[#C6D5B8] bg-[#EDF3E6]',
    warn: 'text-[#A66A00] border-[#E5D2AD] bg-[#FBF3E4]',
    bad: 'text-[#8B2E2E] border-[#E2C6C0] bg-[#F9EDEB]',
    plain: 'text-[#3D4434] border-[#D5CCB8] bg-[#FCFAF3]',
  }
  return (
    <div className={`rounded border px-3 py-2 ${tones[tone]}`}>
      <div className="text-[10px] uppercase tracking-widest text-[#777B68]">{label}</div>
      <div className="mt-0.5 text-lg font-semibold leading-tight">{value}</div>
      {sub && <div className="text-[10px] text-[#777B68]">{sub}</div>}
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
      <header className="app-header flex items-center justify-between bg-[#3B4A2F] px-4 pb-3 pt-2">
        <div>
          <div className="text-sm font-semibold tracking-widest text-[#F7F3E7]">DRONA-LOG · SIH26251</div>
          <div className="text-[10px] uppercase tracking-widest text-[#C9C0A8]">
            Predictive logistics &amp; forward supply chain · Ladakh sector
          </div>
        </div>
        <div className="flex items-center gap-4 text-[11px]">
          {plan && (
            <span className="text-[#C9C0A8]">
              SOLVE <span className="text-[#F3D9A4]">{plan.status}</span> · {fmtNum(plan.solve_time_ms, 0)} ms
            </span>
          )}
          <span className="rounded border border-[#C9A45C] bg-[#F7ECD6] px-2 py-0.5 text-[#8A5A18]">SIMULATED DATA</span>
          <button onClick={load} className="rounded border border-[#8E9A7A] px-2 py-0.5 text-[#F7F3E7] hover:bg-[#4A5A3C]">REFRESH</button>
          <Link to="/edge" className="rounded border border-[#D9B978] bg-[#F3D9A4] px-2 py-0.5 font-semibold text-[#3B4A2F] hover:bg-[#EACB8B]">EDGE CLIENT →</Link>
        </div>
      </header>

      {err && (
        <div className="border-b border-[#E2C6C0] bg-[#F5E3E0] px-4 py-1 text-[11px] text-[#8B2E2E]">
          {err}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* left: map */}
        <div className="relative min-w-0 flex-1">
          {network && <TacticalMap network={network} plan={plan} closure={closedPass} onFobClick={setSelFob} />}
          {/* pass closure controls */}
          <div className="absolute left-3 top-3 rounded border border-[#CFC5AE] bg-[#F7F3E8]/95 p-3 backdrop-blur">
            <div className="mb-2 text-[10px] uppercase tracking-widest text-[#777B68]">Pass closure simulation</div>
            <div className="flex flex-col gap-1.5">
              <button
                disabled={busy || closedPass === 'ZOJILA'}
                onClick={() => closePass('ZOJILA', 'closed')}
                className="rounded border border-[#B5766B] bg-[#F5E3E0] px-3 py-1 text-[11px] text-[#8B2E2E] hover:bg-[#EDD3CE] disabled:opacity-40"
              >
                CLOSE ZOJI LA (west corridor)
              </button>
              <button
                disabled={busy || closedPass !== 'ZOJILA'}
                onClick={() => closePass('ZOJILA', 'reopened')}
                className="rounded border border-[#7C9A6A] bg-[#E7EFE2] px-3 py-1 text-[11px] text-[#3F6B3F] hover:bg-[#D8E5CE] disabled:opacity-40"
              >
                REOPEN ZOJI LA
              </button>
              {busy && <div className="text-[11px] text-[#A66A00]">rerouting + re-solving…</div>}
            </div>
            {closedPass && (
              <div className="mt-2 border-t border-[#CFC5AE] pt-2 text-[11px] text-[#8B2E2E]">
                ZOJI LA CLOSED — west corridor severed. Inflow via Manali (south).
              </div>
            )}
          </div>
          <div className="absolute bottom-3 left-3 rounded border border-[#CFC5AE] bg-[#F7F3E8]/95 px-3 py-2 text-[10px] text-[#5C624F] backdrop-blur">
            <div className="mb-1 flex items-center gap-3">
              <span><span className="inline-block h-2 w-2 rounded-full bg-[#3F6B3F]" /> base depot</span>
              <span><span className="inline-block h-2 w-2 rounded-full bg-[#2F6F77]" /> staging</span>
              <span><span className="inline-block h-2 w-2 rounded-full bg-[#C08A2E]" /> FOB</span>
              <span><span className="inline-block h-2 w-2 rounded-full bg-[#8B2E2E]" /> closed pass</span>
            </div>
            <div>corridor: <span className="text-[#3F6B3F]">green</span> open · <span className="text-[#8B2E2E]">red</span> closed · <span className="text-[#2F6F77]">teal</span> Manali · dashed amber = planned convoys</div>
          </div>
        </div>

        {/* right: panels */}
        <aside className="flex w-[420px] flex-col overflow-y-auto border-l border-[#D5CCB8] bg-[#F7F3E8]">
          {/* closure diff */}
          {closure && (
            <section className="border-b border-[#D5CCB8] p-3">
              <div className="mb-2 flex items-center justify-between">
                <div className="text-[11px] uppercase tracking-widest text-[#8B2E2E]">Pass closure impact — {closure.pass.id}</div>
                <button onClick={() => setClosure(null)} className="text-[#777B68] hover:text-[#3D4434]">✕</button>
              </div>
              <div className="grid grid-cols-2 gap-2 text-[11px]">
                <div className="rounded border border-[#CFC5AE] p-2">
                  <div className="text-[#777B68]">Reroute time</div>
                  <div className="text-base text-[#8A5A18]">{fmtNum(closure.router_time_ms, 1)} ms</div>
                </div>
                <div className="rounded border border-[#CFC5AE] p-2">
                  <div className="text-[#777B68]">Re-solve time</div>
                  <div className="text-base text-[#8A5A18]">{fmtNum(closure.solve_time_ms, 1)} ms</div>
                </div>
              </div>
              <div className="mt-2 grid grid-cols-3 gap-2 text-center text-[11px]">
                <div className="rounded border border-[#E2C6C0] bg-[#F9EDEB] p-1.5">
                  <div className="text-[#777B68]">Cancelled</div>
                  <div className="text-[#8B2E2E]">{closure.diff.counts.removed}</div>
                </div>
                <div className="rounded border border-[#C6D5B8] bg-[#EDF3E6] p-1.5">
                  <div className="text-[#777B68]">Added</div>
                  <div className="text-[#3F6B3F]">{closure.diff.counts.added}</div>
                </div>
                <div className="rounded border border-[#E5D2AD] bg-[#FBF3E4] p-1.5">
                  <div className="text-[#777B68]">Changed</div>
                  <div className="text-[#8A5A18]">{closure.diff.counts.changed}</div>
                </div>
              </div>
              {/* inventory dip: LEH stock projection old vs new */}
              <div className="mt-3 text-[11px]">
                <div className="mb-1 text-[#777B68]">LEH depot projection (class I) — before vs after</div>
                <DipChart
                  before={closure.old_plan?.depot_projection?.LEH?.I}
                  after={closure.new_plan?.depot_projection?.LEH?.I}
                />
                <div className="mt-1 text-[#777B68]">
                  Alternate gateway: Manali → Leh, {fmtNum(closure.gateway_routes?.south?.distance_km, 0)} km, cost-km {fmtNum(closure.gateway_routes?.south?.cost_km, 0)}
                </div>
              </div>
            </section>
          )}

          {/* metrics */}
          {metrics?.delta && (
            <section className="border-b border-[#D5CCB8] p-3">
              <div className="mb-2 flex items-center justify-between">
                <div className="text-[11px] uppercase tracking-widest text-[#5C624F]">Impact vs naive baseline</div>
                <span className="text-[9px] text-[#A66A00]">30-day sim · {metrics.label}</span>
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
              <div className="mt-2 text-[10px] leading-relaxed text-[#777B68]">
                Naive: nearest-depot resupply, 3-day fixed lead, no forecast/rerouting. DRONA: LightGBM + MILP, 1-day lead, corridor-aware. Includes Zoji La closure days {metrics.scenario.closure.from_day}–{metrics.scenario.closure.to_day}. All figures computed from the simulation.
              </div>
            </section>
          )}

          {/* plan */}
          {plan && (
            <section className="border-b border-[#D5CCB8] p-3">
              <div className="mb-2 flex items-baseline justify-between">
                <div className="text-[11px] uppercase tracking-widest text-[#5C624F]">7-day allocation plan</div>
                <div className="text-[10px] text-[#777B68]">
                  {plan.status} · obj {fmtINR(plan.objective_inr)} · {plan.kpi.trucks} trucks
                </div>
              </div>
              <div className="max-h-56 overflow-y-auto text-[11px]">
                <table className="w-full">
                  <thead className="text-[#777B68]">
                    <tr><th className="text-left font-normal">D</th><th className="text-left font-normal">From → To</th><th className="text-left font-normal">Load (I / III / V)</th><th className="text-right font-normal">Cost</th></tr>
                  </thead>
                  <tbody>
                    {plan.deliveries.map((d, i) => (
                      <tr key={i} className="border-t border-[#E3DCCB]">
                        <td className="py-0.5 text-[#777B68]">D{d.day}</td>
                        <td className="text-[#3D4434]">{d.from} → {d.to}</td>
                        <td className="text-[#5C624F]">{d.classes.I ?? '·'} / {d.classes.III ?? '·'} / {d.classes.V ?? '·'}</td>
                        <td className="text-right text-[#5C624F]">{fmtINR(d.cost_inr)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-2 flex gap-3 text-[10px] text-[#777B68]">
                <span>stockouts: <span className="text-[#3F6B3F]">{fmtNum(plan.kpi.stockout_units, 2)} units</span></span>
                <span>transport: {fmtINR(plan.kpi.transport_inr)}</span>
                <span>solve: {fmtNum(plan.solve_time_ms, 0)} ms</span>
              </div>
            </section>
          )}

          {/* FOB stock list */}
          <section className="flex-1 p-3">
            <div className="mb-2 text-[11px] uppercase tracking-widest text-[#5C624F]">FOB stock vs safety buffer</div>
            <div className="space-y-2">
              {network?.nodes.filter((n) => n.kind === 'fob').map((n) => {
                const st = fobStock[n.id] || {}
                const worst = Math.min(...['I', 'III', 'V'].map((c) => {
                  const s = st[c]
                  if (!s || !s.buffer) return 100
                  return (s.on_hand / s.buffer) * 100
                }))
                const tone = worst < 50 ? '#A83A32' : worst < 100 ? '#B7791F' : '#3F6B3F'
                return (
                  <button
                    key={n.id}
                    onClick={() => setSelFob(selFob === n.id ? null : n.id)}
                    className={`block w-full rounded border px-2 py-1.5 text-left ${selFob === n.id ? 'border-[#B08D57] bg-[#FBF3E4]' : 'border-[#DED6C3] hover:bg-[#EDE6D6]'}`}
                  >
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="text-[#3D4434]">{n.id} <span className="text-[#8A8D7A]">{n.elev_m} m · {n.troops} pax</span></span>
                      <span style={{ color: tone }}>{fmtNum(worst, 0)}% of buffer</span>
                    </div>
                    <div className="mt-1"><Bar pct={worst} color={tone} /></div>
                    {selFob === n.id && selStock && (
                      <div className="mt-2 space-y-1 border-t border-[#DED6C3] pt-2 text-[10px] text-[#5C624F]">
                        {['I', 'III', 'V'].map((c) => {
                          const s = selStock[c]
                          const fc = selForecast?.forecast?.[c]
                          return (
                            <div key={c} className="flex justify-between">
                              <span>{CLASS_META[c].name}</span>
                              <span>
                                {fmtNum(s?.on_hand, 1)} / buffer {fmtNum(s?.buffer, 1)} {CLASS_META[c].unit}
                                {fc && <span className="text-[#8A8D7A]"> · 7d fc {fmtNum(fc.total, 1)}</span>}
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
  if (!before || !after) return <div className="text-[#8A8D7A]">no projection</div>
  const max = Math.max(...before, ...after) || 1
  const rows = before.map((b, i) => ({ b, a: after[i], i }))
  return (
    <div className="flex h-16 items-end gap-1">
      {rows.map((r) => (
        <div key={r.i} className="flex h-full flex-1 items-end gap-px">
          <div
            className="w-1/2 bg-[#8A8D7A]"
            style={{ height: `${Math.max(3, (r.b / max) * 100)}%` }}
            title={`D${r.i}: ${fmtNum(r.b, 1)} before`}
          />
          <div
            className="w-1/2 bg-[#C08A2E]"
            style={{ height: `${Math.max(3, (r.a / max) * 100)}%` }}
            title={`D${r.i}: ${fmtNum(r.a, 1)} after`}
          />
        </div>
      ))}
    </div>
  )
}

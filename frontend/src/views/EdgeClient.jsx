import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import {
  GENESIS, appendEntry, computeHash, getAllEntries, markSynced, openDB, verifyChain,
} from '../edge/ledger.js'

const CLASSES = ['I', 'III', 'V']
const CLASS_NAMES = { I: 'I · Rations (t)', III: 'III · POL (kl)', V: 'V · Ammo (t)' }
const ENTRY_TYPES = ['requisition', 'receipt']

export default function EdgeClient() {
  const [db, setDb] = useState(null)
  const [entries, setEntries] = useState([])
  const [online, setOnline] = useState(navigator.onLine)
  const [backendUp, setBackendUp] = useState(null)
  const [syncResult, setSyncResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ fob_id: 'DBO', class_id: 'I', qty: 1.0, entry_type: 'requisition', note: '' })
  const [fobs, setFobs] = useState(['DBO', 'SASOMA', 'TURTUK', 'HANLE', 'DEMCHOK', 'NYOMA', 'CHUSHUL', 'DURBUK', 'SPANGMIK', 'DISKIT'])
  const [chainCheck, setChainCheck] = useState(null)
  const dbRef = useRef(null)

  const refresh = useCallback(async () => {
    if (!dbRef.current) return
    const all = await getAllEntries(dbRef.current)
    setEntries(all)
    setChainCheck(await verifyChain(all))
  }, [])

  useEffect(() => {
    openDB().then((d) => { dbRef.current = d; setDb(d); refresh() })
  }, [refresh])

  // online/offline + backend heartbeat
  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    const beat = setInterval(async () => {
      if (!navigator.onLine) { setBackendUp(false); return }
      try {
        const r = await fetch('/api/health', { signal: AbortSignal.timeout(1500) })
        setBackendUp(r.ok)
      } catch { setBackendUp(false) }
    }, 2000)
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); clearInterval(beat) }
  }, [])

  // pull FOB list when backend is reachable (cached in state; offline keeps last)
  useEffect(() => {
    if (backendUp && !fobs.length) {
      api('/api/network').then((n) => {
        setFobs(n.nodes.filter((x) => x.kind === 'fob').map((x) => x.id))
      }).catch(() => {})
    }
  }, [backendUp, fobs.length])

  const pending = useMemo(() => entries.filter((e) => !e.synced), [entries])
  const device_id = useMemo(() => {
    let id = localStorage.getItem('drona-edge-device')
    if (!id) {
      id = `edge-${crypto.randomUUID().slice(0, 8)}`
      localStorage.setItem('drona-edge-device', id)
    }
    return id
  }, [])

  const submit = async (ev) => {
    ev.preventDefault()
    if (!dbRef.current) return
    await appendEntry(dbRef.current, form)
    setForm({ ...form, qty: 1.0, note: '' })
    refresh()
  }

  const doSync = async () => {
    if (!dbRef.current || !pending.length || backendUp === false) return
    setBusy(true)
    try {
      const payload = pending.map((e) => ({
        id: e.id, seq: e.seq, prev_hash: e.prev_hash, hash: e.hash,
        payload_json: e.payload_json, fob_id: e.fob_id, class_id: e.class_id,
        qty: e.qty, entry_type: e.entry_type, client_ts: e.client_ts,
      }))
      const res = await api('/api/sync', { method: 'POST', body: JSON.stringify({ device_id, entries: payload }) })
      setSyncResult(res)
      if (res.accepted > 0) {
        const okIds = res.entries.accepted.filter((a) => a.applied).map((a) => a.id)
        await markSynced(dbRef.current, okIds)
      }
      refresh()
    } catch (e) {
      setSyncResult({ error: e.message })
    }
    setBusy(false)
  }

  // dev-only: simulate hand-tampering with the last entry's qty
  const tamperLast = async () => {
    if (!dbRef.current || !entries.length) return
    const last = entries[entries.length - 1]
    const store = dbRef.current.transaction('ledger', 'readwrite').objectStore('ledger')
    const req = store.get(last.seq)
    req.onsuccess = () => {
      const e = req.result
      e.qty = Number((e.qty + 3.7).toFixed(2))   // "edited by hand" - hash NOT recomputed
      e.synced = false                            // force it through sync verification
      store.put(e)
      store.transaction.oncomplete = () => setTimeout(refresh, 50)
    }
  }

  const resetLedger = async () => {
    if (!dbRef.current) return
    const store = dbRef.current.transaction('ledger', 'readwrite').objectStore('ledger')
    store.clear()
    store.transaction.oncomplete = () => { setSyncResult(null); refresh() }
  }

  const statusColor = backendUp === null ? 'text-slate-400' : backendUp ? 'text-emerald-400' : 'text-rose-400'
  const statusText = backendUp === null ? 'PROBING' : backendUp ? 'BACKEND ONLINE' : (online ? 'BACKEND OFFLINE' : 'OFFLINE')

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-[#1b2735] bg-[#0c1219] px-4 py-2">
        <div>
          <div className="text-sm font-semibold tracking-widest text-slate-200">DRONA-LOG · EDGE CLIENT</div>
          <div className="text-[10px] uppercase tracking-widest text-slate-500">offline requisitions · hash-chained ledger · {device_id}</div>
        </div>
        <div className="flex items-center gap-4 text-[11px]">
          <span className={statusColor}>
            <span className={`mr-1.5 inline-block h-2 w-2 rounded-full ${backendUp ? 'bg-emerald-400' : 'bg-rose-500'}`} />
            {statusText}
          </span>
          <Link to="/" className="rounded border border-slate-700 px-2 py-0.5 text-slate-300 hover:bg-[#16202c]">← COMMAND VIEW</Link>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* left: entry form + ledger */}
        <div className="flex min-w-0 flex-1 flex-col p-4">
          <form onSubmit={submit} className="mb-4 grid grid-cols-6 items-end gap-2 rounded border border-[#1d2836] bg-[#0d141c] p-3">
            <label className="col-span-2">
              <div className="text-[10px] uppercase text-slate-500">FOB</div>
              <select value={form.fob_id} onChange={(e) => setForm({ ...form, fob_id: e.target.value })}
                className="mt-1 w-full rounded border border-[#28364a] bg-[#0a0f14] px-2 py-1 text-[12px] text-slate-200">
                {fobs.map((f) => <option key={f}>{f}</option>)}
              </select>
            </label>
            <label className="col-span-2">
              <div className="text-[10px] uppercase text-slate-500">Supply class</div>
              <select value={form.class_id} onChange={(e) => setForm({ ...form, class_id: e.target.value })}
                className="mt-1 w-full rounded border border-[#28364a] bg-[#0a0f14] px-2 py-1 text-[12px] text-slate-200">
                {CLASSES.map((c) => <option key={c} value={c}>{CLASS_NAMES[c]}</option>)}
              </select>
            </label>
            <label>
              <div className="text-[10px] uppercase text-slate-500">Qty</div>
              <input type="number" step="0.1" min="0" value={form.qty} required
                onChange={(e) => setForm({ ...form, qty: parseFloat(e.target.value) })}
                className="mt-1 w-full rounded border border-[#28364a] bg-[#0a0f14] px-2 py-1 text-[12px] text-slate-200" />
            </label>
            <label>
              <div className="text-[10px] uppercase text-slate-500">Type</div>
              <select value={form.entry_type} onChange={(e) => setForm({ ...form, entry_type: e.target.value })}
                className="mt-1 w-full rounded border border-[#28364a] bg-[#0a0f14] px-2 py-1 text-[12px] text-slate-200">
                {ENTRY_TYPES.map((t) => <option key={t}>{t}</option>)}
              </select>
            </label>
            <label className="col-span-4">
              <div className="text-[10px] uppercase text-slate-500">Note</div>
              <input value={form.note} placeholder="e.g. winter stock, exercise draw"
                onChange={(e) => setForm({ ...form, note: e.target.value })}
                className="mt-1 w-full rounded border border-[#28364a] bg-[#0a0f14] px-2 py-1 text-[12px] text-slate-200" />
            </label>
            <button type="submit"
              className="col-span-2 rounded border border-amber-700 bg-amber-950/40 px-3 py-1.5 text-[12px] text-amber-300 hover:bg-amber-950/70">
              RECORD LOCALLY →
            </button>
          </form>

          {/* ledger table */}
          <div className="min-h-0 flex-1 overflow-y-auto rounded border border-[#1d2836] bg-[#0b1118]">
            {entries.length === 0 ? (
              <div className="p-6 text-center text-[11px] text-slate-600">
                Local ledger empty. Entries are hash-chained and stored in IndexedDB -
                they survive page reloads and backend outages.
              </div>
            ) : (
              <table className="w-full text-[11px]">
                <thead className="sticky top-0 bg-[#0d141c] text-slate-500">
                  <tr>
                    <th className="px-2 py-1 text-left font-normal">#</th>
                    <th className="px-2 py-1 text-left font-normal">Time</th>
                    <th className="px-2 py-1 text-left font-normal">Type</th>
                    <th className="px-2 py-1 text-left font-normal">FOB · Class</th>
                    <th className="px-2 py-1 text-right font-normal">Qty</th>
                    <th className="px-2 py-1 text-left font-normal">Hash</th>
                    <th className="px-2 py-1 text-left font-normal">Sync</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((e) => {
                    const bad = chainCheck && !chainCheck.ok && chainCheck.at.seq === e.seq
                    return (
                      <tr key={e.seq} className={`border-t border-[#141d28] ${bad ? 'bg-rose-950/30' : ''}`}>
                        <td className="px-2 py-1 text-slate-500">{e.seq}</td>
                        <td className="px-2 py-1 text-slate-500">{e.client_ts.slice(5, 16).replace('T', ' ')}</td>
                        <td className={`px-2 py-1 ${e.entry_type === 'receipt' ? 'text-emerald-400' : 'text-amber-400'}`}>{e.entry_type}</td>
                        <td className="px-2 py-1 text-slate-300">{e.fob_id} · {e.class_id}</td>
                        <td className="px-2 py-1 text-right text-slate-300">{e.qty}</td>
                        <td className="px-2 py-1 font-mono text-slate-600">{e.hash.slice(0, 10)}…</td>
                        <td className="px-2 py-1">
                          {e.synced
                            ? <span className="text-emerald-500">synced</span>
                            : <span className="text-slate-500">pending</span>}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
          {chainCheck && !chainCheck.ok && (
            <div className="mt-2 rounded border border-rose-900/60 bg-rose-950/40 px-3 py-2 text-[11px] text-rose-300">
              LOCAL CHAIN BROKEN at seq {chainCheck.at.seq}: hash does not match content.
              Sync will reject this entry (and everything after it).
            </div>
          )}
        </div>

        {/* right: sync panel */}
        <aside className="w-[340px] border-l border-[#1b2735] bg-[#0b1118] p-4">
          <div className="mb-3 text-[11px] uppercase tracking-widest text-slate-400">Sync</div>
          <div className="mb-3 grid grid-cols-2 gap-2 text-center text-[11px]">
            <div className="rounded border border-[#233140] bg-[#0d141c] p-2">
              <div className="text-slate-500">Local entries</div>
              <div className="text-lg text-slate-200">{entries.length}</div>
            </div>
            <div className="rounded border border-[#233140] bg-[#0d141c] p-2">
              <div className="text-slate-500">Pending sync</div>
              <div className={`text-lg ${pending.length ? 'text-amber-400' : 'text-emerald-400'}`}>{pending.length}</div>
            </div>
          </div>
          <button onClick={doSync} disabled={busy || !pending.length || backendUp === false}
            className="mb-2 w-full rounded border border-cyan-700 bg-cyan-950/40 px-3 py-2 text-[12px] text-cyan-300 hover:bg-cyan-950/70 disabled:opacity-40">
            {backendUp === false ? 'BACKEND DOWN — CANNOT SYNC' : `SYNC ${pending.length} ENTRIES`}
          </button>
          <button onClick={tamperLast} disabled={!entries.length}
            className="mb-2 w-full rounded border border-rose-800 bg-rose-950/30 px-3 py-1.5 text-[10px] text-rose-400 hover:bg-rose-950/60 disabled:opacity-40">
            SIMULATE HAND-EDIT OF LAST ENTRY (tamper test)
          </button>
          <button onClick={resetLedger}
            className="mb-4 w-full rounded border border-slate-700 px-3 py-1.5 text-[10px] text-slate-500 hover:bg-[#141d28]">
            CLEAR LOCAL LEDGER (reset demo)
          </button>

          {syncResult && (
            <div className={`rounded border p-3 text-[11px] ${syncResult.error ? 'border-rose-900/60 bg-rose-950/40 text-rose-300' : syncResult.rejected ? 'border-amber-900/60 bg-amber-950/30 text-amber-300' : 'border-emerald-900/60 bg-emerald-950/30 text-emerald-300'}`}>
              {syncResult.error ? syncResult.error : (
                <>
                  <div className="mb-1 font-semibold">
                    SYNC: {syncResult.accepted} accepted · {syncResult.rejected} rejected
                  </div>
                  {syncResult.reason && <div className="mb-2 text-rose-300">{syncResult.reason}</div>}
                  {syncResult.entries?.rejected?.map((r) => (
                    <div key={r.id} className="text-rose-300">
                      seq {r.seq} ({r.id.slice(0, 8)}…): {r.errors.join('; ')}
                    </div>
                  ))}
                  {syncResult.entries?.accepted?.some((a) => a.note) && (
                    <div className="text-slate-400">duplicates detected and skipped</div>
                  )}
                </>
              )}
            </div>
          )}

          <div className="mt-6 space-y-2 text-[10px] leading-relaxed text-slate-600">
            <p>Each entry stores sha256(seq | prev_hash | payload | fob | class | type | qty).
              Editing any field without re-hashing breaks the chain — the server recomputes
              every hash at sync and rejects tampered entries.</p>
            <p>Try it: stop the backend, record entries, restart, sync — all accepted.
              Then hand-edit one (button above) and sync — it is rejected.</p>
          </div>
        </aside>
      </div>
    </div>
  )
}

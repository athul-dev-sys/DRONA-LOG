const BASE = import.meta.env.DEV ? '' : ''

export async function api(path, opts) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* keep */ }
    throw new Error(`API ${path}: ${detail}`)
  }
  return res.json()
}

export const fmtINR = (v) =>
  v == null ? '—' : `₹${Math.round(v).toLocaleString('en-IN')}`

export const fmtNum = (v, d = 1) =>
  v == null ? '—' : Number(v).toFixed(d)

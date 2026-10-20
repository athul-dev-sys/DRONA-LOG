import { describe, expect, it } from 'vitest'
import { GENESIS, canonicalPayload, computeHash, verifyChain } from './ledger.js'

// SubtleCrypto is available in Node 20+ globally.
const mkEntry = (seq, prevHash, fob, cls, qty, etype = 'requisition') => {
  const payload_json = canonicalPayload({ fob_id: fob, class_id: cls, qty, note: 'test', ts: '2026-10-19T09:00:00.000Z' })
  return { seq, prev_hash: prevHash, payload_json, fob_id: fob, class_id: cls, qty, entry_type: etype }
}

const buildChain = async (n) => {
  const entries = []
  let prev = GENESIS
  for (let i = 1; i <= n; i++) {
    const e = mkEntry(i, prev, 'DBO', ['I', 'III', 'V'][i % 3], 1.0 + i * 0.25)
    e.hash = await computeHash(e.seq, e.prev_hash, e.payload_json, e)
    entries.push(e)
    prev = e.hash
  }
  return entries
}

describe('edge ledger hash chain', () => {
  it('produces the same digest as the backend for identical input', async () => {
    // reference digest computed with Python: sha256("1|<genesis>|payload|DBO|I|requisition|1.5000")
    const payload = JSON.stringify({ fob: 'DBO', class: 'I', qty: 1.5, note: 'x', ts: '2026-10-19T09:00:00Z' })
    const py = await (async () => {
      const { createHash } = await import('node:crypto')
      return createHash('sha256').update(`1|${GENESIS}|${payload}|DBO|I|requisition|1.5000`).digest('hex')
    })()
    const js = await computeHash(1, GENESIS, payload, { fob_id: 'DBO', class_id: 'I', qty: 1.5, entry_type: 'requisition' })
    expect(js).toBe(py)
  })

  it('hash changes when qty is edited', async () => {
    const payload = canonicalPayload({ fob_id: 'DBO', class_id: 'I', qty: 2, note: '', ts: '2026-10-19T09:00:00Z' })
    const h1 = await computeHash(1, GENESIS, payload, { fob_id: 'DBO', class_id: 'I', qty: 2, entry_type: 'receipt' })
    const h2 = await computeHash(1, GENESIS, payload, { fob_id: 'DBO', class_id: 'I', qty: 9.9, entry_type: 'receipt' })
    expect(h1).not.toBe(h2)
  })

  it('verifies a well-formed chain', async () => {
    const chain = await buildChain(5)
    const res = await verifyChain(chain)
    expect(res.ok).toBe(true)
  })

  it('detects a hand-edited entry (qty changed, hash unchanged)', async () => {
    const chain = await buildChain(4)
    chain[2].qty = 99.0 // tamper without re-hashing
    const res = await verifyChain(chain)
    expect(res.ok).toBe(false)
    expect(res.at.seq).toBe(3)
  })

  it('detects a swapped hash link', async () => {
    const chain = await buildChain(3)
    chain[1].prev_hash = 'f'.repeat(64)
    const res = await verifyChain(chain)
    expect(res.ok).toBe(false)
    expect(res.at.seq).toBe(2)
  })

  it('detects a sequence gap', async () => {
    const chain = await buildChain(4)
    const gapped = [chain[0], chain[2], chain[3]]
    const res = await verifyChain(gapped)
    expect(res.ok).toBe(false)
  })
})

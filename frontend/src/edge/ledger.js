// Edge ledger: append-only, hash-chained (SHA-256), persisted in IndexedDB.
// Same hash scheme as the backend sync endpoint:
//   sha256(`${seq}|${prevHash}|${payloadJson}|${fobId}|${classId}|${entryType}|${qty.toFixed(4)}`)
export const GENESIS = '0'.repeat(64)

export async function sha256Hex(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

export function canonicalPayload({ fob_id, class_id, qty, note, ts }) {
  return JSON.stringify({ fob: fob_id, class: class_id, qty, note, ts })
}

export async function computeHash(seq, prevHash, payloadJson, { fob_id, class_id, qty, entry_type }) {
  const body = `${seq}|${prevHash}|${payloadJson}|${fob_id}|${class_id}|${entry_type}|${Number(qty).toFixed(4)}`
  return sha256Hex(body)
}

// ---- IndexedDB store ----
const DB_NAME = 'drona-edge'
const STORE = 'ledger'
const META = 'meta'

export function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 2)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) {
        const s = db.createObjectStore(STORE, { keyPath: 'seq' })
        s.createIndex('synced', 'synced')
      }
      if (!db.objectStoreNames.contains(META)) db.createObjectStore(META)
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

function tx(db, store, mode) {
  return db.transaction(store, mode).objectStore(store)
}

export function putEntries(db, entries) {
  return new Promise((resolve, reject) => {
    const store = tx(db, STORE, 'readwrite')
    entries.forEach((e) => store.put(e))
    store.transaction.oncomplete = () => resolve()
    store.transaction.onerror = () => reject(store.transaction.error)
  })
}

export function getAllEntries(db) {
  return new Promise((resolve, reject) => {
    const req = tx(db, STORE, 'readonly').getAll()
    req.onsuccess = () => resolve(req.result.sort((a, b) => a.seq - b.seq))
    req.onerror = () => reject(req.error)
  })
}

export function setMeta(db, key, value) {
  return new Promise((resolve, reject) => {
    const req = tx(db, META, 'readwrite').put(value, key)
    req.onsuccess = () => resolve()
    req.onerror = () => reject(req.error)
  })
}

export function getMeta(db, key) {
  return new Promise((resolve, reject) => {
    const req = tx(db, META, 'readonly').get(key)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

/** Verify the full local chain. Returns {ok, firstBad} - used to show local
 *  tampering before sync (the sync endpoint re-verifies authoritatively). */
export async function verifyChain(entries) {
  let prev = GENESIS
  for (const e of entries) {
    if (e.seq !== (entries[0].seq + entries.indexOf(e))) {
      return { ok: false, at: e }
    }
    if (e.prev_hash !== prev) return { ok: false, at: e }
    const h = await computeHash(e.seq, e.prev_hash, e.payload_json, e)
    if (h !== e.hash) return { ok: false, at: e }
    prev = e.hash
  }
  return { ok: true, at: null }
}

/** Append a new entry to the local chain. Returns the stored entry. */
export async function appendEntry(db, { fob_id, class_id, qty, entry_type, note }) {
  const entries = await getAllEntries(db)
  const seq = entries.length + 1
  const prevHash = entries.length ? entries[entries.length - 1].hash : GENESIS
  const ts = new Date().toISOString()
  const payload_json = canonicalPayload({ fob_id, class_id, qty, note, ts })
  const hash = await computeHash(seq, prevHash, payload_json, { fob_id, class_id, qty, entry_type })
  const entry = {
    id: crypto.randomUUID(), seq, prev_hash: prevHash, hash, payload_json,
    fob_id, class_id, qty, entry_type, client_ts: ts, synced: false,
  }
  await putEntries(db, [entry])
  return entry
}

export async function markSynced(db, ids) {
  const entries = await getAllEntries(db)
  const updated = entries.filter((e) => ids.includes(e.id)).map((e) => ({ ...e, synced: true }))
  if (updated.length) await putEntries(db, updated)
  return updated.length
}

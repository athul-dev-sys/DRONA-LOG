"""Acceptance criteria 3 & 4 (backend side):
- Edge client creates entries with the backend stopped; syncs correctly after
  restart (simulated here against a stopped API session).
- Editing a ledger entry by hand makes sync reject it.
Also directly tests the hash-chain primitives the edge client uses.
"""
import hashlib
import json
import uuid

GENESIS = "0" * 64


def _mkhash(seq, prev, payload, qty, fob, cls, etype):
    body = f"{seq}|{prev}|{payload}|{fob}|{cls}|{etype}|{qty:.4f}"
    return hashlib.sha256(body.encode()).hexdigest()


def _entry(seq, prev_hash, fob, cls, qty, etype="requisition", note="offline"):
    payload = json.dumps({"fob": fob, "class": cls, "qty": qty, "note": note,
                          "ts": "2026-10-19T09:00:00Z"})
    return {
        "id": f"ent-{uuid.uuid4().hex[:8]}",
        "seq": seq, "prev_hash": prev_hash,
        "hash": _mkhash(seq, prev_hash, payload, qty, fob, cls, etype),
        "payload_json": payload, "fob_id": fob, "class_id": cls,
        "qty": qty, "entry_type": etype, "client_ts": "2026-10-19T09:00:00Z",
    }


def _chain(n, fob="DBO"):
    entries, prev = [], GENESIS
    for i in range(1, n + 1):
        e = _entry(i, prev, fob, ["I", "III", "V"][i % 3], 1.0 + i * 0.1)
        entries.append(e)
        prev = e["hash"]
    return entries


def test_sync_after_backend_restart(client):
    """AC3: entries created while backend was 'stopped' sync correctly once it
    is back. The client kept signing entries with its local chain the whole time."""
    device = f"edge-ac3-{uuid.uuid4().hex[:6]}"
    entries = _chain(5)   # all created during the "outage"

    r = client.post("/api/sync", json={"device_id": device, "entries": entries})
    assert r.status_code == 200
    d = r.json()
    assert d["accepted"] == 5
    assert d["rejected"] == 0
    assert d["chain_head_seq"] == 5

    # ledger holds them in order
    rows = client.get("/api/ledger").json()
    mine = [x for x in rows if x["id"] in {e["id"] for e in entries}]
    assert len(mine) == 5
    assert [x["seq"] for x in mine] == [1, 2, 3, 4, 5]


def test_sync_receipt_updates_stock(client):
    device = f"edge-receipt-{uuid.uuid4().hex[:6]}"
    before = client.get("/api/fobs").json()
    on_hand_before = {r["on_hand"] for r in before
                      if r["id"] == "HANLE" and r["class_id"] == "I"}.pop()
    e = _entry(1, GENESIS, "HANLE", "I", 2.5, etype="receipt")
    r = client.post("/api/sync", json={"device_id": device, "entries": [e]})
    assert r.json()["accepted"] == 1
    after = client.get("/api/fobs").json()
    on_hand_after = {r["on_hand"] for r in after
                     if r["id"] == "HANLE" and r["class_id"] == "I"}.pop()
    assert on_hand_after == on_hand_before + 2.5


def test_tampered_entry_rejected(client):
    """AC4: editing a ledger entry by hand makes sync reject it."""
    device = f"edge-ac4-{uuid.uuid4().hex[:6]}"
    entries = _chain(4)

    # sync the first three untouched
    r = client.post("/api/sync", json={"device_id": device, "entries": entries[:3]})
    assert r.json()["accepted"] == 3

    # "edit by hand": change qty after the entry was signed
    tampered = dict(entries[3])
    tampered["qty"] = 99.0
    r2 = client.post("/api/sync", json={"device_id": device, "entries": [tampered]})
    d = r2.json()
    assert d["rejected"] == 1
    assert d["accepted"] == 0
    assert "HASH MISMATCH" in d["reason"]

    # the tampered entry was NOT applied
    rows = client.get("/api/ledger").json()
    assert all(x["qty"] != 99.0 for x in rows)

    # and the genuine entry can still sync afterwards (chain not corrupted)
    r3 = client.post("/api/sync", json={"device_id": device,
                                        "entries": [entries[3]]})
    assert r3.json()["accepted"] == 1


def test_tampered_payload_rejected(client):
    """Editing payload_json by hand is also caught (hash covers payload too)."""
    device = f"edge-ac4b-{uuid.uuid4().hex[:6]}"
    good = _entry(1, GENESIS, "DBO", "I", 2.0)
    r = client.post("/api/sync", json={"device_id": device, "entries": [good]})
    assert r.json()["accepted"] == 1
    bad = _entry(2, good["hash"], "DBO", "I", 2.0)
    bad["payload_json"] = bad["payload_json"].replace('"qty": 2.0', '"qty": 500.0')
    assert '"qty": 500.0' in bad["payload_json"], "tamper edit must take effect"
    r2 = client.post("/api/sync", json={"device_id": device, "entries": [bad]})
    d = r2.json()
    assert d["rejected"] == 1
    assert "HASH MISMATCH" in d["reason"]


def test_replay_is_idempotent(client):
    device = f"edge-replay-{uuid.uuid4().hex[:6]}"
    entries = _chain(3)
    client.post("/api/sync", json={"device_id": device, "entries": entries})
    r = client.post("/api/sync", json={"device_id": device, "entries": entries})
    d = r.json()
    assert d["rejected"] == 0
    rows = client.get("/api/ledger").json()
    mine = [x for x in rows if x["id"] == entries[0]["id"]]
    assert len(mine) == 1, "no duplicate rows on replay"


def test_tampered_replay_rejected(client):
    """Editing an already-synced entry and replaying it is still caught; a
    duplicate id cannot be used to overwrite or bypass the chain."""
    device = f"edge-replay-tamper-{uuid.uuid4().hex[:6]}"
    entries = _chain(2)
    assert client.post("/api/sync", json={"device_id": device,
                                          "entries": entries}).json()["accepted"] == 2
    tampered = dict(entries[0])
    tampered["qty"] = 77.0
    d = client.post("/api/sync", json={"device_id": device,
                                       "entries": [tampered]}).json()
    assert d["accepted"] == 0
    assert d["rejected"] == 1
    assert "HASH MISMATCH" in d["reason"]
    rows = client.get("/api/ledger").json()
    assert all(x["qty"] != 77.0 for x in rows)


def test_sequence_gap_rejected(client):
    device = f"edge-gap-{uuid.uuid4().hex[:6]}"
    entries = _chain(5)
    r = client.post("/api/sync", json={"device_id": device,
                                       "entries": entries[:2]})
    assert r.json()["accepted"] == 2
    # skip 3, send 4 -> gap
    r2 = client.post("/api/sync", json={"device_id": device,
                                        "entries": [entries[3]]})
    d = r2.json()
    assert d["rejected"] == 1
    errors = d["entries"]["rejected"][0]["errors"]
    assert any("sequence gap" in e for e in errors)


def test_broken_prev_hash_rejected(client):
    device = f"edge-link-{uuid.uuid4().hex[:6]}"
    entries = _chain(3)
    client.post("/api/sync", json={"device_id": device, "entries": entries[:1]})
    forged = dict(entries[1])
    forged["prev_hash"] = "f" * 64
    forged["hash"] = _mkhash(forged["seq"], forged["prev_hash"],
                             forged["payload_json"], forged["qty"],
                             forged["fob_id"], forged["class_id"],
                             forged["entry_type"])
    r = client.post("/api/sync", json={"device_id": device, "entries": [forged]})
    d = r.json()
    assert d["rejected"] == 1
    assert "prev_hash" in d["reason"]

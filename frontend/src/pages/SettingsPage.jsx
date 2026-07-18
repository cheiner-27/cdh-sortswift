import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Field, Msg, useMeta, useMsg } from '../components.jsx'

const PICK_FIELD_LABELS = {
  condition: 'Condition (NM→DMG)', name: 'Card name (A–Z)', set_code: 'Set code',
  bin: 'Bin', collector_number: 'Collector #', printing: 'Printing',
}

export default function SettingsPage() {
  const meta = useMeta()
  const [msg, ok, err] = useMsg()
  const [s, setS] = useState(null)
  const [shipFrom, setShipFrom] = useState('')

  useEffect(() => {
    api.get('/api/settings').then((data) => {
      setS(data)
      setShipFrom(JSON.stringify(data.ship_from_address || {
        name: '', street1: '', city: '', state: '', zip: '', country: 'US',
      }, null, 2))
    })
  }, [])

  const save = async () => {
    try {
      let ship
      try { ship = JSON.parse(shipFrom) } catch { throw new Error('ship-from address is not valid JSON') }
      const updated = await api.put('/api/settings', { ...s, ship_from_address: ship })
      setS(updated)
      ok('Settings saved')
    } catch (e) { err(e) }
  }

  if (!s) return null
  const upd = (k, v) => setS({ ...s, [k]: v })

  return (
    <div>
      <h2>Settings</h2>
      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Scanning</h3>
        <div className="row">
          <Field label="Default scan folder"><input style={{ width: 380 }} value={s.scan_folder}
            onChange={(e) => upd('scan_folder', e.target.value)} placeholder="C:\Scans" /></Field>
          <Field label="Min resolution (shorter edge px)"><input style={{ width: 80 }} type="number"
            value={s.min_scan_resolution} onChange={(e) => upd('min_scan_resolution', Number(e.target.value))} /></Field>
          <Field label="Confidence threshold (0-1)"><input style={{ width: 70 }}
            value={s.confidence_threshold} onChange={(e) => upd('confidence_threshold', Number(e.target.value))} /></Field>
          <Field label="Phash max distance"><input style={{ width: 60 }} type="number"
            value={s.phash_max_distance} onChange={(e) => upd('phash_max_distance', Number(e.target.value))} /></Field>
        </div>
        <ul className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          <li><b>Min resolution</b> — scans whose shorter edge is below this (px) are flagged "low res" because OCR tends to fail on them. It's a warning, not a rejection.</li>
          <li><b>Confidence threshold</b> — a recognized card scoring below this (0–1) is routed to "needs review" instead of being auto-accepted.</li>
          <li><b>Phash max distance</b> — the perceptual-hash fallback (used when OCR can't read a card) compares the scan's image fingerprint to catalog images. This is the largest Hamming distance still treated as a possible match: <b>lower = stricter</b> (fewer, closer matches), higher = looser (more candidates, more false matches). ~10–14 is a sensible range; raise it if good scans find no candidates, lower it if you get junk matches.</li>
        </ul>
        <div className="row">
          <Field label="Tesseract path (blank = PATH)"><input style={{ width: 380 }} value={s.tesseract_cmd}
            onChange={(e) => upd('tesseract_cmd', e.target.value)}
            placeholder="C:\Program Files\Tesseract-OCR\tesseract.exe" /></Field>
        </div>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>API keys & shipping</h3>
        <div className="row">
          <Field label="Shippo API token"><input type="password" style={{ width: 300 }} value={s.shippo_api_token}
            onChange={(e) => upd('shippo_api_token', e.target.value)} /></Field>
          <label style={{ alignSelf: 'center' }}><input type="checkbox" checked={s.shippo_test_mode}
            onChange={(e) => upd('shippo_test_mode', e.target.checked)} /> Shippo test mode</label>
        </div>
        <div className="row">
          <Field label="Auto-label threshold: skip label for orders ≤ $"><input style={{ width: 70 }}
            value={s.label_min_order_value} onChange={(e) => upd('label_min_order_value', Number(e.target.value))} /></Field>
          <Field label="Ship-from address (JSON, Shippo format)">
            <textarea rows={6} style={{ width: 420 }} value={shipFrom}
              onChange={(e) => setShipFrom(e.target.value)} /></Field>
        </div>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>eBay</h3>
        <div className="row">
          <Field label="Marketplace ID"><input style={{ width: 110 }} value={s.ebay_marketplace_id}
            onChange={(e) => upd('ebay_marketplace_id', e.target.value)} /></Field>
          <Field label="Merchant location key"><input style={{ width: 220 }} value={s.ebay_merchant_location_key}
            onChange={(e) => upd('ebay_merchant_location_key', e.target.value)} /></Field>
        </div>
        <p className="muted">The merchant location must exist as a Business Location in eBay Seller Hub first — a missing location is the most common cause of publish failures. Credentials live on the Marketplaces page.</p>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Behavior</h3>
        <div className="row">
          <Field label="Large-move flag % (reprice preview)"><input style={{ width: 70 }}
            value={s.large_move_pct} onChange={(e) => upd('large_move_pct', Number(e.target.value))} /></Field>
          <Field label="Import undo window (minutes)"><input style={{ width: 70 }} type="number"
            value={s.import_undo_window_minutes} onChange={(e) => upd('import_undo_window_minutes', Number(e.target.value))} /></Field>
          <Field label="Default expense tax rate (e.g. 0.06 = 6%)"><input style={{ width: 80 }}
            value={s.default_expense_tax_rate} onChange={(e) => upd('default_expense_tax_rate', Number(e.target.value))} /></Field>
        </div>
        <ul className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          <li><b>Large-move flag %</b> — purely a visual safety flag in the Pricing "Simulate" preview: any card whose new price differs from its current price by at least this percent gets a red badge (and the "large moves only" filter shows just those), so a bad reprice is easy to spot before you commit. It does <i>not</i> block or cap prices — that's the per-tier "max move %" guard.</li>
          <li><b>Import undo window</b> — how many minutes after a CSV import you can still one-click undo the whole batch.</li>
        </ul>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Pick list</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Order the merged pick list to match how you organize stock. Rows sort by these
          fields top-to-bottom (the first is the primary sort). Custom / non-catalog and
          unmatched lines always fall to the end.
        </p>
        <PickSortEditor value={s.pick_list_sort || []} options={meta?.pick_sort_fields || []}
          onChange={(v) => upd('pick_list_sort', v)} />
      </div>

      <div className="row center">
        <button className="primary" onClick={save}>Save all settings</button>
        <Msg msg={msg} />
      </div>
    </div>
  )
}

function PickSortEditor({ value, options, onChange }) {
  const list = value || []
  const move = (i, d) => {
    const j = i + d
    if (j < 0 || j >= list.length) return
    const n = [...list];[n[i], n[j]] = [n[j], n[i]]; onChange(n)
  }
  const remove = (i) => onChange(list.filter((_, j) => j !== i))
  const add = (f) => { if (f && !list.includes(f)) onChange([...list, f]) }
  const unused = options.filter((o) => !list.includes(o))
  return (
    <div>
      <ol style={{ listStyle: 'none', paddingLeft: 0, margin: '4px 0' }}>
        {list.map((f, i) => (
          <li key={f} className="row center" style={{ gap: 6, marginBottom: 4 }}>
            <span className="muted" style={{ minWidth: 20, textAlign: 'right' }}>{i + 1}.</span>
            <b style={{ minWidth: 160 }}>{PICK_FIELD_LABELS[f] || f}</b>
            <button className="small" disabled={i === 0} onClick={() => move(i, -1)}>▲</button>
            <button className="small" disabled={i === list.length - 1} onClick={() => move(i, 1)}>▼</button>
            <button className="small danger" onClick={() => remove(i)}>✕</button>
          </li>))}
      </ol>
      {list.length === 0 && <p className="muted">No fields selected — the pick list falls back to condition, then A–Z by name.</p>}
      {unused.length > 0 && (
        <div className="row center">
          <span className="muted">Add field:</span>
          <select value="" onChange={(e) => { add(e.target.value); e.target.value = '' }}>
            <option value="">choose…</option>
            {unused.map((o) => <option key={o} value={o}>{PICK_FIELD_LABELS[o] || o}</option>)}
          </select>
        </div>)}
    </div>
  )
}

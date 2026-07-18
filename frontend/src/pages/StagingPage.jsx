import React, { useEffect, useState } from 'react'
import { api, fmtMoney, scanImageUrl } from '../api.js'
import { CardSearch, Field, Modal, Msg, SortTh, useMeta, useMsg, useSort } from '../components.jsx'

export default function StagingPage() {
  const meta = useMeta()
  const [msg, ok, err] = useMsg()
  const [rows, setRows] = useState([])
  const { sorted, sort, toggle: sortBy } = useSort(rows)
  const [source, setSource] = useState('')
  const [selected, setSelected] = useState(new Set())
  const [showAdd, setShowAdd] = useState(false)
  const [simResults, setSimResults] = useState(null)

  const refresh = () =>
    api.get(`/api/staging${source ? `?source=${source}` : ''}`).then(setRows)
  useEffect(() => { refresh() }, [source])

  const toggle = (id) => setSelected((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
  })

  const patch = async (id, payload) => {
    const updated = await api.patch(`/api/staging/${id}`, payload)
    setRows((list) => list.map((r) => (r.id === id ? updated : r)))
  }

  const approve = async (all) => {
    try {
      const res = await api.post('/api/staging/approve',
        all ? {} : { ids: [...selected] })
      ok(`Approved ${res.approved} row(s) → live inventory`)
      setSelected(new Set()); refresh()
    } catch (e) { err(e) }
  }
  const reject = async () => {
    if (!window.confirm('Rejecting permanently discards these rows. Continue?')) return
    const res = await api.post('/api/staging/reject', { ids: [...selected] })
    ok(`Discarded ${res.rejected} row(s)`)
    setSelected(new Set()); refresh()
  }

  const previewPricing = async (marketplace) => {
    try {
      const r = await api.post(`/api/pricing/simulate/${marketplace}`, {})
      setSimResults({ marketplace, results: r.results.slice(0, 100) })
    } catch (e) { err(e) }
  }

  if (!meta) return null
  return (
    <div>
      <h2>Staging <span className="muted">(pre-commit review)</span></h2>
      <div className="row center">
        <Field label="Source"><select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="">all</option><option>scan</option><option>csv</option><option>manual</option>
        </select></Field>
        <button onClick={() => setShowAdd(true)}>+ Add cards</button>
        <button onClick={() => previewPricing('ebay')}>Reprice preview (eBay)</button>
        <span style={{ flex: 1 }} />
        <button className="primary" disabled={!selected.size} onClick={() => approve(false)}>
          Approve selected ({selected.size})</button>
        <button className="primary" onClick={() => approve(true)}>Approve all</button>
        <button className="danger" disabled={!selected.size} onClick={reject}>Reject selected</button>
      </div>
      <Msg msg={msg} />

      <div className="panel table-wrap"><table>
        <thead><tr>
          <th><input type="checkbox" checked={selected.size === rows.length && rows.length > 0}
            onChange={() => setSelected(selected.size === rows.length ? new Set() : new Set(rows.map((r) => r.id)))} /></th>
          <SortTh k="name" accessor={(r) => r.card ? r.card.name : r.custom_name || ''} sort={sort} toggle={sortBy}>Item</SortTh>
          <SortTh k="source" accessor={(r) => r.source} sort={sort} toggle={sortBy}>Source</SortTh>
          <SortTh k="cond" accessor={(r) => r.condition} sort={sort} toggle={sortBy}>Cond</SortTh>
          <SortTh k="printing" accessor={(r) => r.printing} sort={sort} toggle={sortBy}>Printing</SortTh>
          <SortTh k="lang" accessor={(r) => r.language} sort={sort} toggle={sortBy}>Lang</SortTh>
          <SortTh k="bin" accessor={(r) => r.bin} sort={sort} toggle={sortBy}>Bin</SortTh>
          <SortTh k="qty" accessor={(r) => r.quantity} sort={sort} toggle={sortBy}>Qty</SortTh>
          <SortTh k="cost" accessor={(r) => r.cost} sort={sort} toggle={sortBy}>Cost</SortTh>
          <SortTh k="acq" accessor={(r) => r.acquired_at} sort={sort} toggle={sortBy}>Acquired</SortTh>
          <SortTh k="market" accessor={(r) => r.market_value || 0} sort={sort} toggle={sortBy}>Market</SortTh>
          <SortTh k="price" accessor={(r) => r.price} sort={sort} toggle={sortBy}>Price</SortTh>
          <SortTh k="comment" accessor={(r) => r.comment} sort={sort} toggle={sortBy}>Comment</SortTh>
        </tr></thead>
        <tbody>{sorted.map((r) => (
          <tr key={r.id}>
            <td><input type="checkbox" checked={selected.has(r.id)} onChange={() => toggle(r.id)} /></td>
            <td>
              {r.scan_image_path && <img className="card-img" src={scanImageUrl(r.scan_image_path)} alt="" />}
              <div>{r.card ? r.card.name : r.custom_name || '?'}</div>
              {r.card && <div className="muted">{r.card.set_code} #{r.card.collector_number}</div>}
            </td>
            <td><span className="badge">{r.source}</span></td>
            <td><select value={r.condition} onChange={(e) => patch(r.id, { condition: e.target.value })}>
              {meta.conditions.map((c) => <option key={c}>{c}</option>)}</select></td>
            <td><select value={r.printing} onChange={(e) => patch(r.id, { printing: e.target.value })}>
              {meta.printings.map((p) => <option key={p}>{p}</option>)}</select></td>
            <td><select value={r.language} onChange={(e) => patch(r.id, { language: e.target.value })}>
              {meta.languages.map((l) => <option key={l}>{l}</option>)}</select></td>
            <td><input style={{ width: 70 }} defaultValue={r.bin}
              onBlur={(e) => e.target.value !== r.bin && patch(r.id, { bin: e.target.value })} /></td>
            <td><input type="number" min="1" style={{ width: 55 }} defaultValue={r.quantity}
              onBlur={(e) => patch(r.id, { quantity: Number(e.target.value) || 1 })} /></td>
            <td><input style={{ width: 65 }} defaultValue={r.cost ?? ''}
              onBlur={(e) => patch(r.id, { cost: e.target.value === '' ? null : Number(e.target.value) })} /></td>
            <td><input type="date" style={{ width: 130 }} defaultValue={r.acquired_at ? r.acquired_at.slice(0, 10) : ''}
              onBlur={(e) => patch(r.id, { acquired_at: e.target.value || null })} title="Original purchase date (drives FIFO age)" /></td>
            <td style={{ whiteSpace: 'nowrap' }} title="TCGplayer market value (reference)">
              {r.market_value == null ? <span className="muted">—</span>
                : <span style={{ color: r.market_value >= 2 ? 'var(--green)' : 'var(--muted)' }}>{fmtMoney(r.market_value)}</span>}</td>
            <td><input style={{ width: 65 }} defaultValue={r.price ?? ''}
              onBlur={(e) => patch(r.id, { price: e.target.value === '' ? null : Number(e.target.value) })} /></td>
            <td><input style={{ width: 130 }} defaultValue={r.comment}
              onBlur={(e) => e.target.value !== r.comment && patch(r.id, { comment: e.target.value })} /></td>
          </tr>))}
        </tbody>
      </table>
        {rows.length === 0 && <p className="muted">Nothing staged. Confirmed scans and CSV imports land here before going live.</p>}
      </div>

      {showAdd && <AddCards meta={meta} onClose={() => { setShowAdd(false); refresh() }} />}
      {simResults && (
        <Modal title={`Reprice preview — ${simResults.marketplace}`} onClose={() => setSimResults(null)} wide>
          <table><thead><tr><th>Item</th><th>Old</th><th>New</th><th>Move</th></tr></thead>
            <tbody>{simResults.results.map((r) => (
              <tr key={r.inventory_id}><td>{r.description}</td>
                <td>{fmtMoney(r.old_price)}</td><td>{fmtMoney(r.new_price)}</td>
                <td>{r.move_pct !== null && <span className={`badge ${r.large_move ? 'red' : ''}`}>{r.move_pct}%</span>}</td>
              </tr>))}</tbody></table>
        </Modal>
      )}
    </div>
  )
}

// Single manual-entry path: search the catalog, add one or many cards, set
// printing/condition/language per row, then either stage or go direct to live.
function AddCards({ meta, onClose }) {
  const [msg, ok, err] = useMsg()
  const [rows, setRows] = useState([])
  const [direct, setDirect] = useState(false)
  const [applyAll, setApplyAll] = useState({ printing: '', language: '', quantity: '', price: '', cost: '', bin: '', acquired_at: '' })
  const addCard = (c) => setRows((r) => [...r, {
    catalog_card_id: c.id, name: c.name, set_code: c.set_code, collector_number: c.collector_number,
    condition: 'NM', printing: 'normal', language: 'en',
    quantity: 1, price: '', cost: '', bin: '', acquired_at: '',
  }])
  const setRow = (i, k, v) => setRows((r) => r.map((row, j) => (j === i ? { ...row, [k]: v } : row)))
  const applyToAll = () => setRows((r) => r.map((row) => {
    const patch = {}
    for (const [k, v] of Object.entries(applyAll)) if (v !== '') patch[k] = v
    return { ...row, ...patch }
  }))
  const submit = async () => {
    try {
      const res = await api.post('/api/staging/bulk-add', {
        direct,
        rows: rows.map((r) => ({
          ...r,
          quantity: Number(r.quantity) || 1,
          price: r.price === '' ? null : Number(r.price),
          cost: r.cost === '' ? null : Number(r.cost),
        })),
      })
      ok(direct ? `Added ${res.added} row(s) to live inventory` : `Staged ${res.staged} row(s)`)
      setRows([])
    } catch (e) { err(e) }
  }
  return (
    <Modal title="Add cards" onClose={onClose} wide>
      <p className="muted" style={{ marginTop: 0 }}>Search the catalog and add each card. Set the printing
        (foil / holo / 1st edition / reverse holo) per row — that's the disambiguator a card export can't carry.</p>
      <CardSearch onSelect={addCard} clearOnSelect={false} selectLabel="Add" />
      {rows.length > 0 && (<>
        <div className="row center">
          <span className="muted">Apply to all:</span>
          <select value={applyAll.printing} onChange={(e) => setApplyAll({ ...applyAll, printing: e.target.value })}>
            <option value="">printing…</option>{meta.printings.map((p) => <option key={p}>{p}</option>)}</select>
          <select value={applyAll.language} onChange={(e) => setApplyAll({ ...applyAll, language: e.target.value })}>
            <option value="">lang…</option>{meta.languages.map((l) => <option key={l}>{l}</option>)}</select>
          <input placeholder="qty" style={{ width: 50 }} value={applyAll.quantity} onChange={(e) => setApplyAll({ ...applyAll, quantity: e.target.value })} />
          <input placeholder="price" style={{ width: 60 }} value={applyAll.price} onChange={(e) => setApplyAll({ ...applyAll, price: e.target.value })} />
          <input placeholder="cost" style={{ width: 60 }} value={applyAll.cost} onChange={(e) => setApplyAll({ ...applyAll, cost: e.target.value })} />
          <input placeholder="bin" style={{ width: 65 }} value={applyAll.bin} onChange={(e) => setApplyAll({ ...applyAll, bin: e.target.value })} />
          <input type="date" title="acquired date for all" style={{ width: 130 }} value={applyAll.acquired_at} onChange={(e) => setApplyAll({ ...applyAll, acquired_at: e.target.value })} />
          <button className="small" onClick={applyToAll}>Apply</button>
        </div>
        <div className="table-wrap"><table><thead><tr>
          <th>Card</th><th>Cond</th><th>Printing</th><th>Lang</th><th>Qty</th><th>Price</th><th>Cost</th><th>Acquired</th><th>Bin</th><th></th>
        </tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i}>
              <td>{r.name} <span className="muted">{r.set_code} #{r.collector_number}</span></td>
              <td><select value={r.condition} onChange={(e) => setRow(i, 'condition', e.target.value)}>
                {meta.conditions.map((c) => <option key={c}>{c}</option>)}</select></td>
              <td><select value={r.printing} onChange={(e) => setRow(i, 'printing', e.target.value)}>
                {meta.printings.map((p) => <option key={p}>{p}</option>)}</select></td>
              <td><select value={r.language} onChange={(e) => setRow(i, 'language', e.target.value)}>
                {meta.languages.map((l) => <option key={l}>{l}</option>)}</select></td>
              <td><input style={{ width: 50 }} value={r.quantity} onChange={(e) => setRow(i, 'quantity', e.target.value)} /></td>
              <td><input style={{ width: 60 }} value={r.price} onChange={(e) => setRow(i, 'price', e.target.value)} /></td>
              <td><input style={{ width: 60 }} value={r.cost} onChange={(e) => setRow(i, 'cost', e.target.value)} /></td>
              <td><input type="date" style={{ width: 130 }} value={r.acquired_at} onChange={(e) => setRow(i, 'acquired_at', e.target.value)} /></td>
              <td><input style={{ width: 65 }} value={r.bin} onChange={(e) => setRow(i, 'bin', e.target.value)} /></td>
              <td><button className="small danger" onClick={() => setRows((rows) => rows.filter((_, j) => j !== i))}>✕</button></td>
            </tr>))}</tbody></table></div>
        <div className="row center">
          <label><input type="checkbox" checked={direct} onChange={(e) => setDirect(e.target.checked)} /> skip staging (direct to live inventory)</label>
          <button className="primary" onClick={submit}>{direct ? 'Add' : 'Stage'} {rows.length} row(s)</button>
          <Msg msg={msg} />
        </div>
      </>)}
    </Modal>
  )
}

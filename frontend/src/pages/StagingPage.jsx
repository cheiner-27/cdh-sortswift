import React, { useEffect, useState } from 'react'
import { api, fmtMoney, scanImageUrl } from '../api.js'
import {
  BulkPileSelect, CardSearch, Field, Modal, Msg, SortTh, useBulkPiles, useMeta,
  useMsg, useSort,
} from '../components.jsx'

// source_bulk_id is undefined ("leave alone") rather than '' so the null the
// picker sends for "fresh stock" survives as a real change.
const EMPTY_BULK = {
  acquired_at: '', cost: '', price: '', bin: '',
  condition: '', printing: '', language: '', quantity: '',
  source_bulk_id: undefined,
}

export default function StagingPage() {
  const meta = useMeta()
  const [msg, ok, err] = useMsg()
  const [rows, setRows] = useState([])
  const { sorted, sort, toggle: sortBy } = useSort(rows)
  const [source, setSource] = useState('')
  const [selected, setSelected] = useState(new Set())
  const [bulkSet, setBulkSet] = useState(EMPTY_BULK)
  const [showAdd, setShowAdd] = useState(false)
  const [simResults, setSimResults] = useState(null)
  const { piles, pileOf, pileName, refresh: refreshPiles } = useBulkPiles()

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

  // Same field on every selected row — mostly for stamping one purchase
  // date/cost across a batch that came in together. Blanks are left alone, and
  // the form keeps its values so the next selection can reuse them.
  const applyBulk = async () => {
    const set = {}
    for (const [k, v] of Object.entries(bulkSet)) {
      if (v !== '' && v !== undefined) set[k] = v
    }
    if (!Object.keys(set).length) { err(new Error('Fill at least one field first')); return }
    try {
      const res = await api.post('/api/staging/bulk-edit', { ids: [...selected], set })
      const byId = new Map(res.rows.map((r) => [r.id, r]))
      setRows((list) => list.map((r) => byId.get(r.id) || r))
      ok(`Updated ${res.updated} row(s): ${Object.keys(set).join(', ')}`)
    } catch (e) { err(e) }
  }

  const approve = async (all) => {
    try {
      const res = await api.post('/api/staging/approve',
        all ? {} : { ids: [...selected] })
      // Rows whose bulk pile couldn't be pulled from stay staged — say so
      // rather than reporting a clean run that silently moved nothing.
      const notes = (res.partial || []).map(
        (p) => `row ${p.id}: pile only had ${p.moved} of ${p.wanted}`)
      const reasons = [...new Set((res.skipped || []).map((s) => s.reason))]
      const summary = `Approved ${res.approved} row(s) → live inventory`
      if (reasons.length) {
        err(new Error(`${summary}. Left ${res.skipped.length} staged — ${reasons.join('; ')}`))
      } else {
        ok(notes.length ? `${summary} (${notes.join('; ')})` : summary)
      }
      // Approving a bulk-pull row decrements its pile, so the picker's on-hand
      // counts are stale until reloaded.
      setSelected(new Set()); refresh(); refreshPiles()
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

      {selected.size > 0 && (
        <div className="panel">
          <div className="row center">
            <b style={{ fontSize: 13 }}>Set on {selected.size} selected:</b>
            <input type="date" style={{ width: 130 }} value={bulkSet.acquired_at}
              title="Acquired — original purchase date for the whole batch (drives FIFO age)"
              onChange={(e) => setBulkSet({ ...bulkSet, acquired_at: e.target.value })} />
            <input placeholder="cost" style={{ width: 60 }} value={bulkSet.cost}
              title="Per-card purchase price. Skipped on rows pulled from a bulk pile."
              onChange={(e) => setBulkSet({ ...bulkSet, cost: e.target.value })} />
            <input placeholder="price" style={{ width: 60 }} value={bulkSet.price}
              onChange={(e) => setBulkSet({ ...bulkSet, price: e.target.value })} />
            <input placeholder="bin" style={{ width: 65 }} value={bulkSet.bin}
              onChange={(e) => setBulkSet({ ...bulkSet, bin: e.target.value })} />
            <select value={bulkSet.condition} onChange={(e) => setBulkSet({ ...bulkSet, condition: e.target.value })}>
              <option value="">cond…</option>{meta.conditions.map((c) => <option key={c}>{c}</option>)}</select>
            <select value={bulkSet.printing} onChange={(e) => setBulkSet({ ...bulkSet, printing: e.target.value })}>
              <option value="">printing…</option>{meta.printings.map((p) => <option key={p}>{p}</option>)}</select>
            <select value={bulkSet.language} onChange={(e) => setBulkSet({ ...bulkSet, language: e.target.value })}>
              <option value="">lang…</option>{meta.languages.map((l) => <option key={l}>{l}</option>)}</select>
            <input placeholder="qty" style={{ width: 50 }} value={bulkSet.quantity}
              onChange={(e) => setBulkSet({ ...bulkSet, quantity: e.target.value })} />
            <BulkPileSelect piles={piles} width={180} placeholder="bulk lot…"
              value={bulkSet.source_bulk_id}
              onChange={(v) => setBulkSet({ ...bulkSet, source_bulk_id: v })} />
            <button className="primary" onClick={applyBulk}>Apply</button>
            <button className="small" onClick={() => setBulkSet(EMPTY_BULK)}>Clear form</button>
            <span className="muted" style={{ fontSize: 12 }}>Blank fields are left alone.</span>
          </div>
        </div>
      )}

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
          <SortTh k="bulk" accessor={(r) => pileName(r.source_bulk_id)} sort={sort} toggle={sortBy}
            title="Pulled out of a bulk pile instead of added as fresh stock">From bulk</SortTh>
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
            <td><input key={`bin-${r.id}-${r.bin}`} style={{ width: 70 }} defaultValue={r.bin}
              onBlur={(e) => e.target.value !== r.bin && patch(r.id, { bin: e.target.value })} /></td>
            <td><input key={`qty-${r.id}-${r.quantity}`} type="number" min="1" style={{ width: 55 }} defaultValue={r.quantity}
              onBlur={(e) => patch(r.id, { quantity: Number(e.target.value) || 1 })} /></td>
            <td><BulkPileSelect piles={piles} width={130} compact freshLabel="— fresh —"
              value={r.source_bulk_id ?? null}
              onChange={(v) => patch(r.id, { source_bulk_id: v })} /></td>
            <td>{r.source_bulk_id
              ? <span className="muted" title="Cost comes from the bulk pile this card is pulled from">(from bulk)</span>
              : <input key={`cost-${r.id}-${r.cost ?? ''}`} style={{ width: 65 }} defaultValue={r.cost ?? ''}
                  onBlur={(e) => patch(r.id, { cost: e.target.value === '' ? null : Number(e.target.value) })} />}</td>
            <td>{r.source_bulk_id
              ? <span className="muted" title="Inherited from the bulk pile this card is pulled from — its purchase date, not today's">
                  {pileOf(r.source_bulk_id)?.acquired_at?.slice(0, 10) || '(from bulk)'}</span>
              : <input key={`acq-${r.id}-${r.acquired_at ?? ''}`} type="date" style={{ width: 130 }}
                  defaultValue={r.acquired_at ? r.acquired_at.slice(0, 10) : ''}
                  onBlur={(e) => patch(r.id, { acquired_at: e.target.value || null })} title="Original purchase date (drives FIFO age)" />}</td>
            <td style={{ whiteSpace: 'nowrap' }} title="TCGplayer market value (reference)">
              {r.market_value == null ? <span className="muted">—</span>
                : <span style={{ color: r.market_value >= 2 ? 'var(--green)' : 'var(--muted)' }}>{fmtMoney(r.market_value)}</span>}</td>
            <td><input key={`price-${r.id}-${r.price ?? ''}`} style={{ width: 65 }} defaultValue={r.price ?? ''}
              onBlur={(e) => patch(r.id, { price: e.target.value === '' ? null : Number(e.target.value) })} /></td>
            <td><input key={`cmt-${r.id}-${r.comment}`} style={{ width: 130 }} defaultValue={r.comment}
              onBlur={(e) => e.target.value !== r.comment && patch(r.id, { comment: e.target.value })} /></td>
          </tr>))}
        </tbody>
      </table>
        {rows.length === 0 && <p className="muted">Nothing staged. Confirmed scans and CSV imports land here before going live.</p>}
      </div>

      {showAdd && <AddCards meta={meta} piles={piles}
        onClose={() => { setShowAdd(false); refresh(); refreshPiles() }} />}
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
// Rows can name a bulk pile to come out of, same as a scanning session — sifting
// a pile by hand is the same job as sifting it with the scanner.
function AddCards({ meta, piles, onClose }) {
  const [msg, ok, err] = useMsg()
  const [rows, setRows] = useState([])
  const [direct, setDirect] = useState(false)
  const [applyAll, setApplyAll] = useState({ printing: '', language: '', quantity: '', price: '', cost: '', bin: '', acquired_at: '', source_bulk_id: undefined })
  const addCard = (c) => setRows((r) => [...r, {
    catalog_card_id: c.id, name: c.name, set_code: c.set_code, collector_number: c.collector_number,
    condition: 'NM', printing: 'normal', language: 'en',
    quantity: 1, price: '', cost: '', bin: '', acquired_at: '',
    source_bulk_id: applyAll.source_bulk_id ?? null,
  }])
  const setRow = (i, k, v) => setRows((r) => r.map((row, j) => (j === i ? { ...row, [k]: v } : row)))
  const applyToAll = () => setRows((r) => r.map((row) => {
    const patch = {}
    for (const [k, v] of Object.entries(applyAll)) if (v !== '' && v !== undefined) patch[k] = v
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
          // Cost and acquired date are the pile's on a bulk-pull row — sending
          // what's in the (disabled) boxes would just be ignored downstream.
          cost: (r.source_bulk_id || r.cost === '') ? null : Number(r.cost),
          acquired_at: r.source_bulk_id ? null : r.acquired_at,
        })),
      })
      ok(direct ? `Added ${res.added} row(s) to live inventory` : `Staged ${res.staged} row(s)`)
      setRows([])
    } catch (e) { err(e) }
  }
  return (
    <Modal title="Add cards" onClose={onClose} wide>
      <p className="muted" style={{ marginTop: 0 }}>Search the catalog and add each card. Set the printing
        (foil / holo / 1st edition / reverse holo) per row — that's the disambiguator a card export can't carry.
        Picking a <b>bulk lot</b> below makes every card you then add come out of that pile — the manual
        equivalent of a scanning session's <i>Pull from bulk lot</i>.</p>
      <div className="row center">
        <span className="muted">Pull from bulk lot:</span>
        <BulkPileSelect piles={piles} value={applyAll.source_bulk_id ?? null}
          onChange={(v) => {
            setApplyAll({ ...applyAll, source_bulk_id: v })
            // Retro-apply to rows already added, so picking the pile after
            // adding a few cards doesn't leave those behind as fresh stock.
            setRows((r) => r.map((row) => ({ ...row, source_bulk_id: v })))
          }} />
      </div>
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
          <th>Card</th><th>Cond</th><th>Printing</th><th>Lang</th><th>Qty</th><th>Price</th>
          <th title="Pull this card out of a bulk pile instead of adding fresh stock">From bulk</th>
          <th>Cost</th><th>Acquired</th><th>Bin</th><th></th>
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
              <td><BulkPileSelect piles={piles} width={130} compact freshLabel="— fresh —"
                value={r.source_bulk_id ?? null}
                onChange={(v) => setRow(i, 'source_bulk_id', v)} /></td>
              <td><input style={{ width: 60 }} value={r.source_bulk_id ? '' : r.cost}
                disabled={!!r.source_bulk_id} placeholder={r.source_bulk_id ? 'from bulk' : ''}
                title={r.source_bulk_id ? "Comes from the bulk pile this card is pulled out of" : ''}
                onChange={(e) => setRow(i, 'cost', e.target.value)} /></td>
              <td><input type="date" style={{ width: 130 }} value={r.source_bulk_id ? '' : r.acquired_at}
                disabled={!!r.source_bulk_id}
                title={r.source_bulk_id ? "Inherited from the bulk pile — the day you bought the bulk, not today" : ''}
                onChange={(e) => setRow(i, 'acquired_at', e.target.value)} /></td>
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

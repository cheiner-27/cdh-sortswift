import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, download, fmtMoney } from './api.js'
import { Modal } from './components.jsx'

const base = '/api/inventory/cycle-counts'
const labels = { agree: 'Agrees', accept_tcg: 'Update local', push_local: 'Correct TCG', skip: 'Skipped', excluded: 'Excluded' }

export default function TcgCountReview({ count, onChange, onApproved, err, ok }) {
  const [filter, setFilter] = useState('attention')
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(80)
  const [picker, setPicker] = useState(null)
  const [busy, setBusy] = useState(false)
  const locked = count.status !== 'in_progress'
  const action = async (fn) => {
    setBusy(true)
    try { await fn() } catch (e) { err(e) } finally { setBusy(false) }
  }
  const patch = (line, payload) => action(async () => {
    onChange(await api.patch(`${base}/${count.id}/tcg-lines/${line.id}`, payload))
    setPicker(null)
  })
  const approve = () => {
    if (!window.confirm('Approve this CSV count? Learn the resolved SKU IDs and current prices, and apply only rows marked Update local. TCG corrections are downloaded separately.')) return
    action(async () => {
      const r = await api.post(`${base}/${count.id}/approve`)
      ok(`Approved: ${r.adjusted} local adjustments, ${r.learned} SKU/price records adopted.`)
      onChange(await api.get(`${base}/${count.id}`))
      onApproved()
    })
  }
  const rows = count.lines.filter((l) => {
    const dead = l.counted === 0 && (l.expected === 0 || l.expected === null)
    const attention = !l.ready || (l.inventory_id && l.expected !== l.counted)
    return (filter === 'all' || (filter === 'attention' && attention) ||
      (filter === 'unlinked' && !l.inventory_id && !l.ready) || (filter === 'stocked' && l.counted > 0) || (filter === 'dead' && dead)) &&
      `${l.card_name} ${l.set_name} ${l.sku} ${l.bin || ''}`.toLowerCase().includes(query.toLowerCase())
  })
  const pending = count.lines.filter((l) => !l.ready).length
  return <div className="panel">
    <h3>TCGplayer count #{count.id} · {count.filename}</h3>
    <p className="muted">1. Upload current listings → review each variance → approve → upload quantity corrections to TCGplayer.
      2. Export fresh current listings from TCGplayer and reconcile again to verify, then use Pricing to simulate, reprice and download a price-only CSV.</p>
    <div className="row center">
      <span>{count.lines.length} rows · {pending} needing a decision · {count.corrections} TCG corrections</span>
      {!locked && <>
        <button disabled={busy} onClick={() => {
          action(async () => onChange(await api.post(`${base}/${count.id}/rematch`)))
        }}>Refresh matches</button>
        <button className="primary" disabled={busy || !count.ready} onClick={approve}>Approve reviewed count</button>
      </>}
    </div>
    {count.summary && <p className="muted">
      {count.summary.tcgcsv_matches} linked through TCGCSV · {count.summary.unmatched} unlinked
      {' '}({count.summary.variant_mismatch} condition/finish/language differences, {count.summary.missing_product_link} missing local product links)
      {' '}· {count.summary.ambiguous} ambiguous · {count.summary.variances} quantity variances
      {' '}· {count.summary.conflicts} duplicate links
    </p>}
    {!locked && <p className="muted">Refresh keeps manual matches and unchanged decisions. TCGCSV names and sets are cached daily;
      collector numbers are optional. The first lookup across many sets can take a few minutes.</p>}
    {busy && <p role="status">Working… downloading uncached TCGCSV sets may take a few minutes.</p>}
    {count.catalog?.warnings?.length > 0 && <details><summary>Catalog lookup notices ({count.catalog.warnings.length})</summary>
      <ul>{count.catalog.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul></details>}
    {locked && <div className="panel">
      {count.corrections > 0 && <>
        <p>Upload the correction CSV once. Its quantities are additions or subtractions; uploading it twice repeats the adjustment.
          It retains the prices from this count's source CSV.</p>
        <button disabled={busy || count.corrections_uploaded} onClick={() => action(() => download(`${base}/${count.id}/corrections`))}>
          Download quantity corrections</button>{' '}
        <button disabled={busy || count.corrections_uploaded} onClick={() => {
          if (window.confirm('Have you successfully uploaded this count’s quantity corrections to TCGplayer?'))
            action(async () => onChange(await api.post(`${base}/${count.id}/corrections-uploaded`)))
        }}>{count.corrections_uploaded ? 'Marked uploaded' : 'Mark corrections uploaded'}</button>
      </>}
      <p><Link to="/pricing">Continue to Pricing</Link> after quantities agree. Retained overrides remain available for deliberate clearing.</p>
    </div>}
    <div className="row center">
      <select aria-label="CSV row filter" value={filter} onChange={(e) => { setFilter(e.target.value); setLimit(80) }}>
        <option value="attention">Needs review / variances</option><option value="unlinked">Unlinked / ambiguous</option><option value="stocked">TCG in stock</option>
        <option value="dead">Zero on both sides / dead listings</option><option value="all">All rows (including sealed)</option>
      </select>
      <input placeholder="Find name, set, SKU or bin" value={query} onChange={(e) => { setQuery(e.target.value); setLimit(80) }} />
      <span className="muted">{rows.length} shown by filter</span>
    </div>
    <div className="table-wrap">
      <table><thead><tr><th>TCG listing</th><th>Local record</th><th>Local qty</th><th>TCG qty</th><th>Live price</th><th>Review</th></tr></thead>
        <tbody>{rows.slice(0, limit).map((l) => <tr key={l.id}>
          <td>{l.card_name}<div className="muted">{l.set_name} · {l.collector_number ? `#${l.collector_number}` : 'No collector number'} · {l.raw.Condition} · SKU {l.sku}</div></td>
          <td>{l.label || 'Unlinked'}{l.bin && <div>Bin {l.bin}</div>}
            <div className="muted">{l.match_note}</div>
            {l.price_missing && <div className="badge red">Missing listed price — skip or correct the CSV</div>}
            {l.conflict && <span className="badge red">Same inventory linked twice — resolve or skip</span>}
            {!locked && l.parse_ok && !l.sealed && <button className="small" disabled={busy} onClick={() => setPicker(l)}>Find / change…</button>}
          </td>
          <td>{l.expected ?? '—'}</td><td>{l.counted}</td><td>{fmtMoney(l.listed_price)}</td>
          <td>
            <span className={`badge ${l.ready ? 'green' : 'yellow'}`}>{labels[l.resolution] || 'Decision required'}</span>
            {!locked && l.resolution !== 'excluded' && <div>
              {l.inventory_id && l.expected !== l.counted && <>
                <button className="small" disabled={busy} onClick={() => patch(l, { resolution: 'accept_tcg' })}>Update local to {l.counted}</button>
                <button className="small" disabled={busy} onClick={() => patch(l, { resolution: 'push_local' })}>Correct TCG to {l.expected}</button>
              </>}
              <button className="small" disabled={busy} onClick={() => patch(l, { resolution: 'skip' })}>Skip</button>
            </div>}
          </td>
        </tr>)}</tbody></table>
      {!rows.length && <p>No rows in this view.</p>}
      {rows.length > limit && <button onClick={() => setLimit(limit + 80)}>Show 80 more</button>}
    </div>
    <details><summary>Local stock not linked in this CSV ({count.unlisted.length}) — report only</summary>
      <p className="muted">These may be unlisted or awaiting a manual match. Missing CSV rows never zero local stock.</p>
      <table><thead><tr><th>Item</th><th>Bin</th><th>Quantity</th></tr></thead><tbody>
        {count.unlisted.map((it) => <tr key={it.inventory_id}><td>{it.label}</td><td>{it.bin}</td><td>{it.quantity}</td></tr>)}
      </tbody></table>
    </details>
    {picker && <InventoryPicker line={picker} busy={busy} onClose={() => setPicker(null)}
      onPick={(id) => patch(picker, { inventory_id: id })} err={err} />}
  </div>
}

function InventoryPicker({ line, onPick, onClose, busy, err }) {
  const [q, setQ] = useState(line.card_name)
  const [results, setResults] = useState(null)
  const search = async () => {
    try {
      const r = await api.post('/api/inventory/search', {
        q, condition: line.condition, printing: line.printing_canonical, in_stock_only: false, limit: 60,
      })
      setResults(r.items.map((it) => ({
        inventory_id: it.id, label: `${it.card?.name || it.custom_name} [${it.card?.set_code || ''} #${it.card?.collector_number || ''}] ${it.condition} ${it.printing} ${it.language}`,
        bin: it.bin, quantity: it.quantity,
      })))
    } catch (e) { err(e) }
  }
  return <Modal wide title={`Match ${line.card_name}`} onClose={onClose}>
    <p>{line.set_name} · {line.collector_number ? `#${line.collector_number}` : 'No collector number'} · {line.raw.Condition}. Choose the inventory record whose quantity this listing represents.</p>
    <div className="row center"><input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search()} />
      <button onClick={search} disabled={busy}>Search inventory (including zero stock)</button></div>
    <table><tbody>{(results || line.candidates).map((it) => <tr key={it.inventory_id}>
      <td>{it.label}</td><td>Bin {it.bin || 'unassigned'} · Qty {it.quantity}</td>
      <td><button disabled={busy} onClick={() => onPick(it.inventory_id)}>Use this record</button></td>
    </tr>)}</tbody></table>
    {results?.length === 0 && <p>No inventory match. Add the missing item through normal intake, then refresh matches, or skip this CSV row.</p>}
  </Modal>
}

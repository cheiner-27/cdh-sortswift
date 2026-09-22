import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, download, fmtMoney } from './api.js'
import { Modal } from './components.jsx'

const base = '/api/inventory/cycle-counts'
const labels = { agree: 'Agrees', accept_tcg: 'Update local', push_local: 'Correct TCG', skip: 'Skipped', excluded: 'Excluded' }

export default function TcgCountReview({ count, onChange, onApproved, ok }) {
  const [filter, setFilter] = useState('attention')
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(80)
  const [picker, setPicker] = useState(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')
  const locked = count.status !== 'in_progress'
  const showError = (e) => setActionError(String(e.message || e))
  const action = async (fn) => {
    setActionError('')
    setNotice('')
    setBusy(true)
    try { await fn() } catch (e) { showError(e) } finally { setBusy(false) }
  }
  const patch = (line, payload) => action(async () => {
    onChange(await api.patch(`${base}/${count.id}/tcg-lines/${line.id}`, payload))
    if (payload.inventory_id) setNotice(`Linked ${line.card_name} to record #${payload.inventory_id}.`)
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
      {' '}({count.summary.condition_mismatches ?? 0} condition · {count.summary.printing_mismatches ?? 0} printing · {count.summary.language_mismatches ?? 0} language · {count.summary.missing_product_link} missing product links)
      {' '}· {count.summary.ambiguous} ambiguous · {count.summary.variances} quantity variances
      {' '}· {count.summary.conflicts} duplicate links
    </p>}
    {!locked && <p className="muted">Refresh keeps manual matches and unchanged decisions. TCGCSV names and sets are cached daily;
      collector numbers are optional. The first lookup across many sets can take a few minutes.</p>}
    {busy && <p role="status">Working…</p>}
    {actionError && !picker && <p className="error-text" role="alert">{actionError}</p>}
    {notice && <p className="success-text" role="status">{notice}</p>}
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
            <div className="muted">{l.review_note || l.match_note}</div>
            <Differences differences={l.issues} />
            {l.price_missing && <div className="badge red">Missing price</div>}
            {l.conflict && <span className="badge red">Duplicate inventory link</span>}
            {!locked && l.parse_ok && !l.sealed && <button className="small" disabled={busy} onClick={() => {
              setActionError(''); setPicker(l)
            }}>Find / change…</button>}
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
    {picker && <InventoryPicker countId={count.id} line={picker} busy={busy} error={actionError}
      clearError={() => setActionError('')}
      onClose={() => { if (!busy) { setPicker(null); setActionError('') } }}
      onPick={(id) => patch(picker, { inventory_id: id })} />}
  </div>
}

function Differences({ differences }) {
  return (differences || []).map((d) => <div key={d.field}>
    <span className="badge yellow">{d.label}</span>{' '}Local {d.local} · {d.source || 'TCG'} {d.tcg}
  </div>)
}

function InventoryPicker({ countId, line, onPick, onClose, busy, error, clearError }) {
  const [q, setQ] = useState(line.card_name)
  const [results, setResults] = useState(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [lastQuery, setLastQuery] = useState(null)
  const search = async (query) => {
    clearError()
    setSearchError('')
    setSearching(true)
    try {
      const r = await api.post(`${base}/${countId}/tcg-lines/${line.id}/candidates`, { q: query })
      setResults(r.items)
      setLastQuery(query)
    } catch (e) { setSearchError(String(e.message || e)) } finally { setSearching(false) }
  }
  const disabled = busy || searching
  return <Modal wide title={`Match ${line.card_name}`} onClose={onClose}>
    <p>{line.set_name} · {line.collector_number ? `#${line.collector_number}` : 'No collector number'} · {line.raw.Condition}</p>
    <p className="muted">Condition, printing and language must agree. If local details are wrong, edit the record, then return and refresh matches.</p>
    {(error || searchError) && <p className="error-text" role="alert">{error || searchError}</p>}
    <div className="row center"><input aria-label="Inventory name" value={q} onChange={(e) => setQ(e.target.value)}
      onKeyDown={(e) => { if (e.key === 'Enter' && !disabled) search(q) }} />
      <button onClick={() => search(q)} disabled={disabled}>Search inventory</button>
      <button onClick={() => search(lastQuery)} disabled={disabled}>Refresh records</button></div>
    <table><tbody>{(results || line.candidates).map((it) => <tr key={it.inventory_id}>
      <td>{it.label}<Differences differences={it.differences} />
        {!it.selectable && !it.differences?.length && <div className="error-text">{it.selection_error}</div>}
      </td><td>Bin {it.bin || 'unassigned'} · Qty {it.quantity}</td>
      <td><button disabled={disabled || it.selectable === false} title={it.selection_error || ''}
        onClick={() => { setSearchError(''); onPick(it.inventory_id) }}>{busy ? 'Saving…' : 'Use this record'}</button>
        <div><Link style={{ color: 'var(--accent)' }} to={`/inventory?item=${it.inventory_id}&count=${countId}`}>Edit inventory record</Link></div>
      </td>
    </tr>)}</tbody></table>
    {results?.length === 0 && <p>No records found. Try the base card name or add the missing inventory.</p>}
  </Modal>
}

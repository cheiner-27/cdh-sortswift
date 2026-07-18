import React, { useEffect, useState } from 'react'
import { api, fmtMoney, scanImageUrl } from '../api.js'
import { Field, Modal, Msg, SortTh, useMeta, useMsg, useSort } from '../components.jsx'

const EMPTY_FILTER = {
  q: '', game: '', set_code: '', condition: '', printing: '', bin: '',
  comment: '', in_stock_only: true, include_deleted: false,
  price_min: '', price_max: '', age_min_days: '',
  marketplace: '', listing_status: '',
}

export default function InventoryPage() {
  const meta = useMeta()
  const [msg, ok, err] = useMsg()
  const [filter, setFilter] = useState(EMPTY_FILTER)
  const [data, setData] = useState({ total: 0, items: [] })
  const { sorted, sort, toggle: sortBy } = useSort(data.items)
  const [selected, setSelected] = useState(new Set())
  const [detail, setDetail] = useState(null)
  const [modal, setModal] = useState(null) // 'bulk' | 'adjust' | 'transfer' | 'split' | 'labels'
  const [labels, setLabels] = useState(null)

  const cleanFilter = () => {
    const f = { ...filter, with_age: true }
    for (const k of ['price_min', 'price_max', 'age_min_days'])
      f[k] = f[k] === '' ? undefined : Number(f[k])
    for (const k of Object.keys(f)) if (f[k] === '' || f[k] === undefined) delete f[k]
    return f
  }

  const search = async () => {
    try {
      setData(await api.post('/api/inventory/search', cleanFilter()))
      setSelected(new Set())
    } catch (e) { err(e) }
  }
  useEffect(() => { search() }, [])

  const toggle = (id) => setSelected((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
  })
  const selFilter = () => ({ ids: [...selected], include_deleted: true })

  const mergeDupes = async () => {
    if (!window.confirm('Merge exact-duplicate rows? This is irreversible.')) return
    try {
      const r = await api.post('/api/inventory/merge-duplicates', { filter: cleanFilter() })
      ok(`Merged ${r.merged_rows} duplicate row(s)`); search()
    } catch (e) { err(e) }
  }

  const printLabels = async (layout) => {
    try {
      const r = await api.post('/api/labels/inventory', {
        filter: selected.size ? selFilter() : cleanFilter(), layout,
      })
      setLabels(r)
      setTimeout(() => window.print(), 300)
    } catch (e) { err(e) }
  }

  // Patch one row and update it in place (no full re-search) — used by the
  // inline price-override editor.
  const patchItem = async (id, payload) => {
    try {
      await api.patch(`/api/inventory/${id}`, payload)
      setData((d) => ({ ...d, items: d.items.map((i) => (i.id === id ? { ...i, ...payload } : i)) }))
    } catch (e) { err(e) }
  }

  const bulkDelete = async () => {
    if (!selected.size) return
    if (!window.confirm(`Delete ${selected.size} selected record(s)? Soft delete — restorable via "show deleted".`)) return
    try {
      for (const id of selected) await api.post(`/api/inventory/${id}/delete`)
      ok(`Deleted ${selected.size} record(s)`)
      search()
    } catch (e) { err(e) }
  }

  if (!meta) return null
  return (
    <div>
      <h2>Inventory</h2>
      <div className="panel">
        <div className="row">
          <Field label="Search"><input style={{ width: 200 }} value={filter.q}
            onChange={(e) => setFilter({ ...filter, q: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && search()} /></Field>
          <Field label="Game"><select value={filter.game} onChange={(e) => setFilter({ ...filter, game: e.target.value })}>
            <option value="">all</option>{meta.games.map((g) => <option key={g}>{g}</option>)}</select></Field>
          <Field label="Set"><input style={{ width: 80 }} value={filter.set_code}
            onChange={(e) => setFilter({ ...filter, set_code: e.target.value })} /></Field>
          <Field label="Condition"><select value={filter.condition} onChange={(e) => setFilter({ ...filter, condition: e.target.value })}>
            <option value="">all</option>{meta.conditions.map((c) => <option key={c}>{c}</option>)}</select></Field>
          <Field label="Printing"><select value={filter.printing} onChange={(e) => setFilter({ ...filter, printing: e.target.value })}>
            <option value="">all</option>{meta.printings.map((p) => <option key={p}>{p}</option>)}</select></Field>
          <Field label="Bin"><input style={{ width: 80 }} value={filter.bin}
            onChange={(e) => setFilter({ ...filter, bin: e.target.value })} /></Field>
          <Field label="Price ≥"><input style={{ width: 60 }} value={filter.price_min}
            onChange={(e) => setFilter({ ...filter, price_min: e.target.value })} /></Field>
          <Field label="Price ≤"><input style={{ width: 60 }} value={filter.price_max}
            onChange={(e) => setFilter({ ...filter, price_max: e.target.value })} /></Field>
          <Field label="Age ≥ days"><input style={{ width: 60 }} value={filter.age_min_days}
            onChange={(e) => setFilter({ ...filter, age_min_days: e.target.value })} /></Field>
          <Field label="Marketplace"><select value={filter.marketplace} onChange={(e) => setFilter({ ...filter, marketplace: e.target.value })}>
            <option value="">—</option>{meta.marketplaces.map((m) => <option key={m}>{m}</option>)}</select></Field>
          <Field label="Listing status"><select value={filter.listing_status} onChange={(e) => setFilter({ ...filter, listing_status: e.target.value })}>
            <option value="">—</option><option>unlisted</option><option>listed</option><option>sold</option><option>error</option></select></Field>
        </div>
        <div className="row center">
          <label><input type="checkbox" checked={filter.in_stock_only}
            onChange={(e) => setFilter({ ...filter, in_stock_only: e.target.checked })} /> in stock only</label>
          <label><input type="checkbox" checked={filter.include_deleted}
            onChange={(e) => setFilter({ ...filter, include_deleted: e.target.checked })} /> show deleted</label>
          <button className="primary" onClick={search}>Search</button>
          <span className="muted">{data.total} record(s)</span>
          <span style={{ flex: 1 }} />
          <button disabled={!selected.size} onClick={() => setModal('bulk')}>Bulk edit</button>
          <button disabled={!selected.size} onClick={() => setModal('adjust')}>Adjust stock</button>
          <button disabled={!selected.size} onClick={() => setModal('transfer')}>Transfer bin</button>
          <button className="danger" disabled={!selected.size} onClick={bulkDelete}>Delete ({selected.size})</button>
          <button onClick={mergeDupes}>Merge duplicates</button>
          <button onClick={() => printLabels('standard')}>Print labels</button>
        </div>
        <Msg msg={msg} />
      </div>

      <div className="panel table-wrap"><table>
        <thead><tr>
          <th><input type="checkbox" checked={selected.size === data.items.length && data.items.length > 0}
            onChange={() => setSelected(selected.size === data.items.length ? new Set() : new Set(data.items.map((i) => i.id)))} /></th>
          <SortTh k="name" accessor={(i) => i.card?.name || i.custom_name || ''} sort={sort} toggle={sortBy}>Item</SortTh>
          <SortTh k="cond" accessor={(i) => i.condition} sort={sort} toggle={sortBy}>Cond</SortTh>
          <SortTh k="printing" accessor={(i) => i.printing} sort={sort} toggle={sortBy}>Printing</SortTh>
          <SortTh k="bin" accessor={(i) => i.bin} sort={sort} toggle={sortBy}>Bin</SortTh>
          <SortTh k="qty" accessor={(i) => i.quantity} sort={sort} toggle={sortBy}>Qty</SortTh>
          <SortTh k="price" accessor={(i) => i.price_override ?? i.current_price} sort={sort} toggle={sortBy}>Price</SortTh>
          <SortTh k="cost" accessor={(i) => i.fifo_cost} sort={sort} toggle={sortBy}>Cost</SortTh>
          <SortTh k="age" accessor={(i) => i.age_days} sort={sort} toggle={sortBy}>Age</SortTh>
          <th>Listings</th><th></th>
        </tr></thead>
        <tbody>{sorted.map((it) => (
          <tr key={it.id} style={it.deleted ? { opacity: 0.45 } : {}}>
            <td><input type="checkbox" checked={selected.has(it.id)} onChange={() => toggle(it.id)} /></td>
            <td>
              <div className="item-cell">
                {it.card?.image_url
                  ? <img className="card-thumb" src={it.card.image_url} alt="" loading="lazy" />
                  : <div className="card-thumb" />}
                <div className="item-text">
                  <div>{it.card ? it.card.name : it.custom_name || '?'} {it.deleted && <span className="badge red">deleted</span>}</div>
                  {it.card && <div className="muted">{it.card.game} · {it.card.set_code} #{it.card.collector_number}</div>}
                  {it.comment && <div className="muted" style={{ fontStyle: 'italic' }}>“{it.comment}”</div>}
                </div>
              </div>
            </td>
            <td>{it.condition}</td>
            <td>{it.printing}</td>
            <td>{it.bin || <span className="muted">—</span>}</td>
            <td>{it.quantity}</td>
            <td>
              <div className="price-cell">
                <input key={`po-${it.id}-${it.price_override ?? ''}`}
                  defaultValue={it.price_override ?? ''} placeholder={fmtMoney(it.current_price)}
                  title="Price override — blank = use the auto price"
                  onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
                  onBlur={(e) => {
                    const raw = e.target.value.trim()
                    const val = raw === '' ? null : Number(raw)
                    if (raw !== '' && Number.isNaN(val)) return
                    if (val !== (it.price_override ?? null)) patchItem(it.id, { price_override: val })
                  }} />
                <span className="muted" style={{ fontSize: 11 }}>
                  {it.price_override !== null
                    ? <>auto {fmtMoney(it.current_price)} · <span className="badge yellow">fixed</span></>
                    : 'auto price'}
                </span>
              </div>
            </td>
            <td>{fmtMoney(it.fifo_cost)}</td>
            <td>{it.age_days ?? '—'}{it.age_days !== null ? 'd' : ''}</td>
            <td>{it.listings.map((l) => (
              <div key={l.id}>
                <span className={`badge ${l.status === 'listed' ? 'green' : l.status === 'error' ? 'red' : ''}`}>
                  {l.marketplace}: {l.status}{l.dirty ? ' *' : ''}</span>
              </div>))}</td>
            <td>
              <button className="small" onClick={async () => setDetail(await api.get(`/api/inventory/${it.id}`))}>Detail</button>
              {it.deleted && <> <button className="small" onClick={async () => { await api.post(`/api/inventory/${it.id}/restore`); search() }}>Restore</button></>}
            </td>
          </tr>))}
        </tbody>
      </table></div>

      {detail && <DetailModal meta={meta} item={detail} onClose={() => { setDetail(null); search() }}
        onSplit={() => { setModal('split') }} onMsg={{ ok, err }} />}
      {modal === 'bulk' && <BulkEditModal meta={meta} selFilter={selFilter()} onClose={() => { setModal(null); search() }} />}
      {modal === 'adjust' && <AdjustModal ids={[...selected]} onClose={() => { setModal(null); search() }} />}
      {modal === 'transfer' && <TransferModal ids={[...selected]} onClose={() => { setModal(null); search() }} />}
      {modal === 'split' && detail && <SplitModal meta={meta} item={detail} onClose={() => { setModal(null); setDetail(null); search() }} />}

      {labels && (
        <div className="print-area">
          <div className="label-grid">
            {labels.labels.map((l, i) => (
              <div className="label" key={i}>
                <b>{l.name}</b><br />
                {l.set_code} #{l.collector_number} · {l.condition} {l.printing !== 'normal' ? l.printing : ''}<br />
                {labels.layout === 'standard' && <>Price: {fmtMoney(l.price)} · Bin: {l.bin}<br /></>}
                SKU: {l.sku}
                {l.comment && <><br /><i>{l.comment}</i></>}
              </div>))}
          </div>
        </div>
      )}
    </div>
  )
}

function DetailModal({ meta, item, onClose, onSplit, onMsg }) {
  const [it, setIt] = useState(item)
  const [sr, setSr] = useState({ mode: 'partial', quantity: 1, amount: '' })
  const patch = async (payload) => {
    await api.patch(`/api/inventory/${it.id}`, payload)
    setIt(await api.get(`/api/inventory/${it.id}`))
  }
  const supplierRefund = async () => {
    try {
      const body = sr.mode === 'full'
        ? { mode: 'full', quantity: Number(sr.quantity) }
        : { mode: 'partial', amount: Number(sr.amount) }
      const r = await api.post(`/api/inventory/${it.id}/supplier-refund`, body)
      onMsg.ok(sr.mode === 'full'
        ? `Returned ${r.units} unit(s) to supplier ($${r.cost_recovered} cost recovered)`
        : `Cost basis lowered $${r.applied} FIFO across ${r.batches} batch(es)`
          + (r.unapplied > 0 ? ` ($${r.unapplied} exceeded cost basis, unapplied)` : ''))
      setIt(await api.get(`/api/inventory/${it.id}`))
    } catch (e) { onMsg.err(e) }
  }
  const syncOne = async (marketplace) => {
    try {
      const r = await api.post(`/api/marketplaces/${marketplace}/sync-item/${it.id}`)
      if (r.error || r.error_code) onMsg.err(new Error(r.error || `${r.error_code}: ${r.error_message}`))
      else onMsg.ok(`${marketplace}: ${r.result}`)
      setIt(await api.get(`/api/inventory/${it.id}`))
    } catch (e) { onMsg.err(e) }
  }
  return (
    <Modal title={it.card ? it.card.name : it.custom_name || `Item #${it.id}`} onClose={onClose} wide>
      <div className="row">
        {it.card?.image_url && <img className="card-img large" src={it.card.image_url} alt="" />}
        {it.scan_image_path && <img className="card-img large" src={scanImageUrl(it.scan_image_path)} alt="scan" />}
        <div>
          {it.card && <p className="muted">{it.card.game} · {it.card.set_name} ({it.card.set_code}) #{it.card.collector_number} · {it.card.rarity}</p>}
          <div className="row">
            <Field label="Condition"><select value={it.condition} onChange={(e) => patch({ condition: e.target.value })}>
              {meta.conditions.map((c) => <option key={c}>{c}</option>)}</select></Field>
            <Field label="Printing"><select value={it.printing} onChange={(e) => patch({ printing: e.target.value })}>
              {meta.printings.map((p) => <option key={p}>{p}</option>)}</select></Field>
            <Field label="Bin"><input style={{ width: 90 }} defaultValue={it.bin}
              onBlur={(e) => e.target.value !== it.bin && patch({ bin: e.target.value })} /></Field>
            <Field label="Price override"><input style={{ width: 80 }} defaultValue={it.price_override ?? ''}
              onBlur={(e) => patch({ price_override: e.target.value === '' ? null : Number(e.target.value) })} /></Field>
            <Field label="Price floor"><input style={{ width: 80 }} defaultValue={it.price_floor ?? ''}
              onBlur={(e) => patch({ price_floor: e.target.value === '' ? null : Number(e.target.value) })} /></Field>
          </div>
          <div className="row">
            <Field label="Comment"><input style={{ width: 320 }} defaultValue={it.comment}
              onBlur={(e) => e.target.value !== it.comment && patch({ comment: e.target.value })} /></Field>
            <button onClick={onSplit}>Split record…</button>
          </div>
          <p>Qty: <b>{it.quantity}</b> · FIFO cost: {fmtMoney(it.fifo_cost)} · Age: {it.age_days ?? '—'}d · Price: {fmtMoney(it.price_override ?? it.current_price)}</p>
        </div>
      </div>

      <h3>Acquisition lots <span className="muted">(FIFO — oldest sells first)</span></h3>
      {(it.acquisitions || []).length === 0
        ? <p className="muted">No cost lots recorded (added without a cost).</p>
        : <table><thead><tr><th>Acquired</th><th>Qty</th><th>Remaining</th><th>Unit cost</th><th>Lot value</th></tr></thead>
          <tbody>{it.acquisitions.map((a) => (
            <tr key={a.id}>
              <td>{a.acquired_at ? a.acquired_at.slice(0, 10) : '—'}</td>
              <td>{a.quantity}</td>
              <td>{a.quantity_remaining}</td>
              <td>{fmtMoney(a.unit_cost)}</td>
              <td className="muted">{fmtMoney(a.quantity_remaining * a.unit_cost)}</td>
            </tr>))}</tbody></table>}
      <p className="muted" style={{ fontSize: 12 }}>Each row is a separate purchase batch with its own date &amp; cost. Sales consume the oldest remaining lot first; that oldest lot also drives inventory age.</p>

      <h3>Supplier refund / return <span className="muted">(a refund to you on a purchase)</span></h3>
      <div className="row center">
        <Field label="Type"><select value={sr.mode} onChange={(e) => setSr({ ...sr, mode: e.target.value })}>
          <option value="partial">partial — keep goods, lower cost basis</option>
          <option value="full">full — return goods, remove from inventory</option>
        </select></Field>
        {sr.mode === 'full'
          ? <Field label="Units to return"><input type="number" min="1" max={it.quantity} style={{ width: 70 }}
            value={sr.quantity} onChange={(e) => setSr({ ...sr, quantity: e.target.value })} /></Field>
          : <Field label="Refund amount ($)"><input style={{ width: 80 }} value={sr.amount}
            onChange={(e) => setSr({ ...sr, amount: e.target.value })} /></Field>}
        <button className="small" onClick={supplierRefund}>Apply</button>
        <span className="muted" style={{ fontSize: 12 }}>
          {sr.mode === 'full'
            ? 'Removes the units and recovers their cost (no P&L hit).'
            : 'Applies the refund FIFO to your oldest cost first, lowering near-term COGS.'}
        </span>
      </div>

      <h3>Marketplace listings</h3>
      <table><thead><tr><th>Marketplace</th><th>Status</th><th>Listed $ / qty</th>
        <th>Cap</th><th>Reserve</th><th>External IDs</th><th></th></tr></thead>
        <tbody>{meta.marketplaces.map((mk) => {
          const l = it.listings.find((x) => x.marketplace === mk) || {}
          return (
            <tr key={mk}>
              <td>{mk}</td>
              <td><span className={`badge ${l.status === 'listed' ? 'green' : l.status === 'error' ? 'red' : ''}`}>{l.status || 'unlisted'}</span>
                {l.error_code && <div className="error-text">{l.error_code}: {l.error_message}</div>}</td>
              <td>{fmtMoney(l.listed_price)} / {l.listed_quantity ?? 0}</td>
              <td><input style={{ width: 55 }} defaultValue={l.listing_cap ?? ''} placeholder="—"
                onBlur={(e) => patch({ listings: [{ marketplace: mk, listing_cap: e.target.value === '' ? null : Number(e.target.value) }] })} /></td>
              <td><input style={{ width: 55 }} defaultValue={l.reserve_quantity ?? 0}
                onBlur={(e) => patch({ listings: [{ marketplace: mk, reserve_quantity: Number(e.target.value) || 0 }] })} /></td>
              <td className="muted" style={{ fontSize: 11 }}>
                {l.ebay_listing_id && <>listing {l.ebay_listing_id}<br /></>}
                {l.ebay_offer_id && <>offer {l.ebay_offer_id}<br /></>}
                {l.tcg_sku_id && <>tcg {l.tcg_sku_id}</>}
              </td>
              <td><button className="small" onClick={() => syncOne(mk)}>Sync now</button></td>
            </tr>)
        })}</tbody></table>
      <p className="muted">Listing cap 0 = excluded from that marketplace ("in-store only"). Reserve holds units back exclusively for that marketplace.</p>

      <h3>History</h3>
      <div className="table-wrap" style={{ maxHeight: 260, overflowY: 'auto' }}>
        <table><thead><tr><th>When</th><th>Type</th><th>Δ</th><th>Cause</th><th>Bin</th><th>Comment</th></tr></thead>
          <tbody>{(it.history || []).map((h) => (
            <tr key={h.id}>
              <td className="muted">{h.created_at?.slice(0, 16).replace('T', ' ')}</td>
              <td><span className="badge">{h.type}</span></td>
              <td>{h.quantity_delta > 0 ? `+${h.quantity_delta}` : h.quantity_delta}</td>
              <td>{h.cause}</td>
              <td>{h.bin_before !== null && h.bin_before !== h.bin_after ? `${h.bin_before || '—'} → ${h.bin_after || '—'}` : ''}</td>
              <td className="muted">{h.comment}</td>
            </tr>))}</tbody></table>
      </div>
    </Modal>
  )
}

function BulkEditModal({ meta, selFilter, onClose }) {
  const [msg, ok, err] = useMsg()
  const [set, setSet] = useState({})
  const [preview, setPreview] = useState(null)
  const upd = (k, v) => setSet((s) => ({ ...s, [k]: v }))
  const run = async (isPreview) => {
    try {
      const r = await api.post('/api/inventory/bulk-edit', { filter: selFilter, set, preview: isPreview })
      if (isPreview) setPreview(r)
      else { ok(`Applied to ${r.affected} record(s)`); setPreview(null) }
    } catch (e) { err(e) }
  }
  return (
    <Modal title="Bulk edit" onClose={onClose} wide>
      <div className="row">
        <Field label="Price"><input style={{ width: 70 }} onChange={(e) => upd('price', e.target.value === '' ? undefined : Number(e.target.value))} /></Field>
        <Field label="Price override"><input style={{ width: 70 }} onChange={(e) => upd('price_override', e.target.value === '' ? undefined : Number(e.target.value))} /></Field>
        <Field label="Price floor"><input style={{ width: 70 }} onChange={(e) => upd('price_floor', e.target.value === '' ? undefined : Number(e.target.value))} /></Field>
        <Field label="Comment"><input style={{ width: 160 }} onChange={(e) => upd('comment', e.target.value)} /></Field>
        <Field label="Bin"><input style={{ width: 80 }} onChange={(e) => upd('bin', e.target.value)} /></Field>
      </div>
      <div className="row">
        <Field label="Condition"><select defaultValue="" onChange={(e) => e.target.value && upd('condition', e.target.value)}>
          <option value="">—</option>{meta.conditions.map((c) => <option key={c}>{c}</option>)}</select></Field>
        <Field label="Printing"><select defaultValue="" onChange={(e) => e.target.value && upd('printing', e.target.value)}>
          <option value="">—</option>{meta.printings.map((p) => <option key={p}>{p}</option>)}</select></Field>
        <Field label="Qty (set)"><input style={{ width: 60 }} onChange={(e) => upd('quantity', e.target.value === '' ? undefined : { set: Number(e.target.value) })} /></Field>
        <Field label="Cost: % of price"><input style={{ width: 60 }} onChange={(e) => upd('cost', e.target.value === '' ? undefined : { pct_of_price: Number(e.target.value) })} /></Field>
        <label style={{ alignSelf: 'center' }}><input type="checkbox" onChange={(e) => upd('cost_overwrite', e.target.checked)} /> overwrite existing cost (default fills blanks only)</label>
      </div>
      <div className="row">
        <Field label="eBay listing cap"><input style={{ width: 60 }} onChange={(e) => upd('ebay_listing_cap', e.target.value === '' ? undefined : Number(e.target.value))} /></Field>
        <Field label="TCG listing cap"><input style={{ width: 60 }} onChange={(e) => upd('tcgplayer_listing_cap', e.target.value === '' ? undefined : Number(e.target.value))} /></Field>
        <label style={{ alignSelf: 'center' }}><input type="checkbox" onChange={(e) => e.target.checked && upd('clear_price_override', true)} /> clear price override</label>
        <label style={{ alignSelf: 'center' }}><input type="checkbox" onChange={(e) => e.target.checked && upd('clear_price_floor', true)} /> clear price floor</label>
      </div>
      <div className="row center">
        <button onClick={() => run(true)}>Preview</button>
        <button className="primary" onClick={() => run(false)}>Apply</button>
        <Msg msg={msg} />
      </div>
      {preview && (<>
        <p className="muted">{preview.affected} record(s) would change:</p>
        <div className="table-wrap" style={{ maxHeight: 240, overflowY: 'auto' }}>
          <table><thead><tr><th>ID</th><th>Changes</th></tr></thead>
            <tbody>{preview.plan.map((p) => (
              <tr key={p.inventory_id}><td>{p.inventory_id}</td>
                <td className="muted">{JSON.stringify(p.changes)}</td></tr>))}</tbody></table>
        </div>
      </>)}
    </Modal>
  )
}

function AdjustModal({ ids, onClose }) {
  const [msg, ok, err] = useMsg()
  const [mode, setMode] = useState('delta')
  const [value, setValue] = useState('')
  const [comment, setComment] = useState('')
  const [damaged, setDamaged] = useState(false)
  const run = async () => {
    try {
      const adjustments = ids.map((id) => ({
        inventory_id: id, comment, damaged,
        ...(mode === 'set' ? { set_quantity: Number(value) } : { delta: Number(value) }),
      }))
      const r = await api.post('/api/inventory/adjust', { adjustments })
      ok(`Adjusted ${r.results.length} record(s)`)
    } catch (e) { err(e) }
  }
  return (
    <Modal title={`Manual stock adjustment (${ids.length} record(s))`} onClose={onClose}>
      <div className="row">
        <Field label="Mode"><select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="delta">adjust by amount (+/−)</option>
          <option value="set">set exact quantity</option></select></Field>
        <Field label="Value"><input style={{ width: 80 }} value={value} onChange={(e) => setValue(e.target.value)} /></Field>
      </div>
      <div className="row">
        <Field label="Comment"><input style={{ width: 300 }} value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
        <label style={{ alignSelf: 'center' }}><input type="checkbox" checked={damaged} onChange={(e) => setDamaged(e.target.checked)} /> damaged</label>
      </div>
      <p className="muted">Deductions clamp at 0 — inventory never goes negative. Every adjustment is logged.</p>
      <div className="row center"><button className="primary" onClick={run}>Apply</button><Msg msg={msg} /></div>
    </Modal>
  )
}

function TransferModal({ ids, onClose }) {
  const [msg, ok, err] = useMsg()
  const [bin, setBin] = useState('')
  const [comment, setComment] = useState('')
  return (
    <Modal title={`Bulk transfer (${ids.length} record(s))`} onClose={onClose}>
      <div className="row">
        <Field label="New bin"><input value={bin} onChange={(e) => setBin(e.target.value)} /></Field>
        <Field label="Comment"><input style={{ width: 240 }} value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
        <button className="primary" onClick={async () => {
          try { const r = await api.post('/api/inventory/transfer', { ids, bin, comment }); ok(`Transferred ${r.transferred}`) }
          catch (e) { err(e) }
        }}>Transfer</button>
      </div>
      <Msg msg={msg} />
    </Modal>
  )
}

function SplitModal({ meta, item, onClose }) {
  const [msg, ok, err] = useMsg()
  const [f, setF] = useState({ quantity: 1, condition: item.condition, printing: item.printing, language: item.language })
  return (
    <Modal title={`Split record #${item.id}`} onClose={onClose}>
      <p className="muted">Peel off units into a new record. At least one of condition / printing / language must differ.</p>
      <div className="row">
        <Field label="Quantity"><input type="number" min="1" max={item.quantity} style={{ width: 70 }}
          value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} /></Field>
        <Field label="Condition"><select value={f.condition} onChange={(e) => setF({ ...f, condition: e.target.value })}>
          {meta.conditions.map((c) => <option key={c}>{c}</option>)}</select></Field>
        <Field label="Printing"><select value={f.printing} onChange={(e) => setF({ ...f, printing: e.target.value })}>
          {meta.printings.map((p) => <option key={p}>{p}</option>)}</select></Field>
        <Field label="Language"><select value={f.language} onChange={(e) => setF({ ...f, language: e.target.value })}>
          {meta.languages.map((l) => <option key={l}>{l}</option>)}</select></Field>
        <button className="primary" onClick={async () => {
          try {
            const r = await api.post(`/api/inventory/${item.id}/split`, { ...f, quantity: Number(f.quantity) })
            ok(`Split → new record #${r.new_inventory_id}`)
          } catch (e) { err(e) }
        }}>Split</button>
      </div>
      <Msg msg={msg} />
    </Modal>
  )
}

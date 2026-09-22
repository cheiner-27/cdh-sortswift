import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Field, Modal } from '../components.jsx'

const ACTIONS = {
  lot_balance: 'Correct remaining cost-lot units', stock_from_count: 'Correct stock to a physical count',
  move_cost: 'Move costs from the wrong printing / condition', add_cost_lot: 'Supply missing cost for existing stock',
  sale_costs: 'Use recorded lot costs for this sale', sale_allocation: 'Resolve missing sale costs',
  cost_review: 'Verify or correct a lot cost', origin_review: 'Link transfers to their original purchase',
  archive: 'Restore or dispose of archived stock', classify_outflow: 'Classify an unlinked outflow',
  journal_opening: 'Document missing opening history',
}
const METRICS = { stock_units: 'Stock units (including archived)', lot_units: 'Units covered by cost lots', remaining_cost: 'Remaining lot cost', sale_cogs: 'Recorded sale COGS', purchase_total: 'Recorded purchase total' }
const FIELDS = { quantity: 'Original / sold units', quantity_remaining: 'Remaining units', unit_cost: 'Unit cost', original_unit_cost: 'Original purchase cost', cogs: 'Sale COGS', printing: 'Printing', condition: 'Condition', origin_kind: 'Origin', source_acquisition_id: 'Original source lot', cost_status: 'Cost status', quantity_delta: 'History quantity change', deleted: 'Archived', kind: 'Movement type', deducted_quantity: 'Units deducted' }
const TABLES = { inventory: 'Inventory', acquisition_log: 'Cost lot', fifo_consumption: 'Cost allocation', order_items: 'Sale line', inventory_log: 'History event' }
const sameCard = (a, b) => a && b && a[0] === b[0] && a[1] === b[1]

function options(issue) {
  switch (issue.code) {
    case 'fifo_balance': return issue.difference > 0 ? ['lot_balance', 'stock_from_count', 'move_cost'] : ['move_cost', 'add_cost_lot', 'stock_from_count']
    case 'order_cogs': return ['sale_costs', 'cost_review']
    case 'sale_allocation': case 'unlinked_cogs': return ['sale_allocation']
    case 'purchase_provenance': return ['origin_review']
    case 'archived_stock': return ['archive']
    case 'unclassified_outflow': return ['classify_outflow']
    case 'stock_history': return ['journal_opening', 'stock_from_count']
    case 'invalid_lot': case 'lot_overallocated': return ['lot_balance', 'cost_review']
    default: return ['cost_review']
  }
}

export default function RepairModal({ issue, onClose, onApplied }) {
  const [data, setData] = useState(null)
  const [form, setForm] = useState({ action: options(issue)[0], reason: '', source: 'existing', cost_status: 'unknown', restore: true, kind: 'adjustment', origin_kind: 'opening_balance' })
  const [ticket, setTicket] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const update = patch => { setForm(f => ({ ...f, ...patch })); setTicket(null); setError('') }
  useEffect(() => {
    api.get('/api/reports/reconciliation/records').then(d => {
      setData(d)
      const orderIds = issue.order_ids || (issue.orders || []).map(o => o.order_id)
      const line = d.sale_lines.find(l => orderIds.includes(l.order_id))
      const lot = d.lots.find(l => issue.acquisition_ids?.includes(l.id) || line?.allocation_lot_ids.includes(l.id))
      const inventoryId = issue.inventory_ids?.[0] || line?.inventory_id || ''
      const item = d.items.find(i => i.id === inventoryId)
      const alternate = d.lots.find(l => sameCard(l.identity, item?.identity) && l.remaining > 0 && JSON.stringify(l.identity) !== JSON.stringify(item?.identity))
      setForm(f => ({ ...f, inventory_id: inventoryId, order_id: orderIds[0] || '', line_id: line?.id || '',
        lot_id: f.action === 'move_cost' ? alternate?.id || lot?.id || '' : lot?.id || '',
        quantity: line ? Math.max(0, line.quantity - (line.allocated || 0)) : Math.abs(issue.difference || 1),
        unit_cost: lot?.unit_cost ?? '', cost_status: ['known', 'estimated'].includes(lot?.cost_status) ? lot.cost_status : lot?.unit_cost > 0 ? 'estimated' : 'unknown', acquired_at: lot?.date || line?.date || new Date().toISOString().slice(0, 10),
        lots: d.lots.filter(l => issue.acquisition_ids?.includes(l.id)).map(l => ({ id: l.id, remaining: l.remaining })),
        links: [], purchase_ids: [], consumption_ids: issue.consumption_ids || [],
      }))
    }).catch(e => setError(e.message || String(e)))
  }, [])
  const preview = async () => {
    setBusy(true); setError(''); setTicket(null)
    try { setTicket(await api.post('/api/reports/reconciliation/preview', form)) }
    catch (e) { setError(e.message || String(e)) }
    finally { setBusy(false) }
  }
  const apply = async () => {
    setBusy(true); setError('')
    try {
      const result = await api.post('/api/reports/reconciliation/apply', ticket)
      await onApplied(result)
      onClose()
    } catch (e) { setError(e.message || String(e)) }
    finally { setBusy(false) }
  }
  const chooseLine = id => {
    const line = data.sale_lines.find(l => l.id === Number(id))
    update({ line_id: Number(id), inventory_id: line?.inventory_id, order_id: line?.order_id,
      quantity: Math.max(0, (line?.quantity || 0) - (line?.allocated || 0)), acquired_at: line?.date,
      lot_id: '', correct_printing: false })
  }
  const chooseLot = id => {
    const lot = data.lots.find(l => l.id === Number(id))
    update({ lot_id: Number(id), unit_cost: lot?.unit_cost ?? '', acquired_at: lot?.date,
      cost_status: ['known', 'estimated', 'unknown'].includes(lot?.cost_status) ? lot.cost_status : 'unknown' })
  }
  const selectedItem = data?.items.find(i => i.id === Number(form.inventory_id))
  const selectedLot = data?.lots.find(l => l.id === Number(form.lot_id))
  const orderIds = issue.order_ids || (issue.orders || []).map(o => o.order_id)
  const relevantLines = data?.sale_lines.filter(l => l.active && (!orderIds.length || orderIds.includes(l.order_id))) || []
  const visibleLots = data?.lots.filter(l => form.action === 'cost_review' ? !issue.acquisition_ids?.length || issue.acquisition_ids.includes(l.id) : sameCard(l.identity, selectedItem?.identity)) || []
  const selectLot = <Field label="Cost lot"><select value={form.lot_id || ''} onChange={e => chooseLot(e.target.value)}>
    <option value="">Choose a lot…</option>{visibleLots.map(l => <option key={l.id} value={l.id}>#{l.id} · {l.label} · {l.remaining} left · {l.date} · ${l.unit_cost}</option>)}
  </select></Field>
  const costFields = <>
    <Field label="Cost evidence"><select value={form.cost_status} onChange={e => update({ cost_status: e.target.value })}>
      <option value="unknown">Unknown — still needs evidence</option><option value="estimated">Estimated — document the method</option><option value="known">Verified — supported by records</option>
    </select></Field>
    {form.cost_status !== 'unknown' && <Field label="Unit cost ($)"><input type="number" min="0" step="0.0001" value={form.unit_cost ?? ''} onChange={e => update({ unit_cost: e.target.value })} /></Field>}
  </>
  const suggest = () => {
    let excess = issue.difference
    const order = [...form.lots].sort((a, b) => {
      const aa = data.lots.find(l => l.id === a.id), bb = data.lots.find(l => l.id === b.id)
      return (aa.unit_cost === 0 ? -1 : 0) - (bb.unit_cost === 0 ? -1 : 0) || b.id - a.id
    })
    const proposed = new Map(form.lots.map(l => [l.id, data.lots.find(b => b.id === l.id).remaining]))
    for (const lot of order) { const take = Math.min(excess, proposed.get(lot.id)); proposed.set(lot.id, proposed.get(lot.id) - take); excess -= take }
    update({ lots: form.lots.map(l => ({ ...l, remaining: proposed.get(l.id) })) })
  }
  return <Modal title="Review reconciliation repair" wide onClose={() => !busy && onClose()}>
    <p>{issue.message}</p>
    <p className="muted">Preview the exact stock, cost and sale effects. Applying saves your explanation and a permanent before/after record.</p>
    {error && <p role="alert" style={{ color: 'var(--red)' }}>{error}</p>}
    {!data ? <p>Loading records…</p> : <>
      <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
        <Field label="Correction"><select value={form.action} onChange={e => update({ action: e.target.value })}>
          {options(issue).map(a => <option key={a} value={a}>{ACTIONS[a]}</option>)}
        </select></Field>
        {['move_cost', 'add_cost_lot', 'stock_from_count', 'journal_opening', 'archive'].includes(form.action) && <Field label="Inventory record"><select value={form.inventory_id || ''} onChange={e => update({ inventory_id: Number(e.target.value) })}>
          <option value="">Choose a record…</option>{data.items.filter(i => issue.code !== 'fifo_balance' || sameCard(i.identity, issue.identity)).map(i => <option key={i.id} value={i.id}>#{i.id} · {i.label} · stock {i.quantity}{i.deleted ? ' (archived)' : ''}</option>)}
        </select></Field>}
        {form.action === 'lot_balance' && <>
          <p>Stock stays unchanged. Set the lot balances that should remain. The total must equal {issue.stock ?? 'the current stock quantity'}.</p>
          {issue.difference > 0 && <button onClick={suggest}>Suggest removing excess units ($0 lots first)</button>}
          <table><thead><tr><th>Lot</th><th>Acquired</th><th>Unit cost</th><th>Currently remaining</th><th>Correct remaining</th></tr></thead>
            <tbody>{form.lots.map(change => { const lot = data.lots.find(l => l.id === change.id); return <tr key={lot.id}>
              <td>#{lot.id} · {lot.origin_kind}</td><td>{lot.date}</td><td>${lot.unit_cost}</td><td>{lot.remaining}</td>
              <td><input aria-label={`Remaining units for lot ${lot.id}`} type="number" min="0" max={lot.quantity} value={change.remaining} onChange={e => update({ lots: form.lots.map(l => l.id === lot.id ? { ...l, remaining: e.target.value } : l) })} /></td>
            </tr> })}</tbody></table>
        </>}
        {['move_cost', 'cost_review'].includes(form.action) && selectLot}
        {['move_cost', 'add_cost_lot', 'stock_from_count'].includes(form.action) && <Field label={form.action === 'stock_from_count' ? 'Physically counted quantity for this row' : 'Units covered by this correction'}>
          <input type="number" min="0" value={form.quantity} onChange={e => update({ quantity: e.target.value })} />
        </Field>}
        {form.action === 'sale_costs' && <>
          <Field label="Order"><select value={form.order_id} onChange={e => update({ order_id: Number(e.target.value) })}>{[...new Set(relevantLines.map(l => l.order_id))].map(id => <option key={id} value={id}>Order #{id}</option>)}</select></Field>
          <p>The sale's COGS will be recomputed from its existing purchase-lot allocations. Quantities and purchase costs stay intact.</p>
        </>}
        {form.action === 'sale_allocation' && <>
          <Field label="Sale line"><select value={form.line_id || ''} onChange={e => chooseLine(e.target.value)}><option value="">Choose a sale…</option>{relevantLines.map(l => <option key={l.id} value={l.id}>Order #{l.order_id} · {l.label} · sold {l.quantity}, costed {l.allocated ?? '?'}</option>)}</select></Field>
          <Field label="Source of the missing cost"><select value={form.source} onChange={e => update({ source: e.target.value })}><option value="existing">An existing purchase lot</option><option value="missing">Missing historical intake / cost record</option></select></Field>
          <Field label="Missing sold units"><input type="number" min="1" value={form.quantity} onChange={e => update({ quantity: e.target.value })} /></Field>
          {form.source === 'existing' ? <>{selectLot}<label><input type="checkbox" checked={!!form.correct_printing} onChange={e => update({ correct_printing: e.target.checked })} /> This is the same purchased card; correct the lot's printing/condition to match the sale.</label></> : <>
            {costFields}
            <Field label="Origin"><select value={form.origin_kind} onChange={e => update({ origin_kind: e.target.value })}><option value="opening_balance">Historical cost correction — no new purchase expense</option><option value="purchase">Omitted purchase supported by an invoice</option></select></Field>
            <Field label="Original acquisition date"><input type="date" value={form.acquired_at} onChange={e => update({ acquired_at: e.target.value })} /></Field>
          </>}
        </>}
        {['cost_review', 'add_cost_lot'].includes(form.action) && <div className="row">{costFields}
          {form.action === 'add_cost_lot' && <Field label="Original acquisition date"><input type="date" value={form.acquired_at} onChange={e => update({ acquired_at: e.target.value })} /></Field>}
        </div>}
        {form.action === 'cost_review' && <>
          {selectedLot && <p>Lot #{selectedLot.id}: {selectedLot.quantity} originally recorded, {selectedLot.remaining} remaining, current unit cost ${selectedLot.unit_cost}.</p>}
          <p><label><input type="checkbox" checked={!!form.include_sales} onChange={e => update({ include_sales: e.target.checked })} /> Also correct this lot's past sale allocations (review the COGS change below).</label></p>
          <p><label><input type="checkbox" checked={!!form.correct_purchase} onChange={e => update({ correct_purchase: e.target.checked })} /> The original purchase amount was wrong; correct it using the evidence in my explanation.</label></p>
          <p className="muted">Transferred child lots keep their own recorded costs. Review those separately if the same correction applies.</p>
        </>}
        {form.action === 'origin_review' && <>
          <p>Link transferred cards to the original bulk purchase. Suggested sources share the acquisition date and cost; verify them against your records. This changes purchase attribution, not stock.</p>
          <button onClick={() => update({ links: data.transfer_candidates.filter(c => c.parent_ids.length === 1 && issue.acquisition_ids?.includes(c.lot_id)).map(c => ({ lot_id: c.lot_id, parent_id: c.parent_ids[0] })) })}>Select lots with one suggested source</button>
          <div style={{ maxHeight: 320, overflowY: 'auto' }}><table><thead><tr><th>Include</th><th>Transferred lot</th><th>Original source</th></tr></thead><tbody>
            {data.transfer_candidates.filter(c => issue.acquisition_ids?.includes(c.lot_id)).map(c => { const lot = data.lots.find(l => l.id === c.lot_id); const link = form.links.find(l => l.lot_id === c.lot_id); return <tr key={c.lot_id}>
              <td><input type="checkbox" checked={!!link} onChange={e => update({ links: e.target.checked ? [...form.links, { lot_id: c.lot_id, parent_id: c.parent_ids[0] }] : form.links.filter(l => l.lot_id !== c.lot_id) })} /></td>
              <td>#{lot.id} · {lot.label} · {lot.quantity} @ ${lot.unit_cost}</td>
              <td><select disabled={!link} value={link?.parent_id || c.parent_ids[0]} onChange={e => update({ links: form.links.map(l => l.lot_id === c.lot_id ? { ...l, parent_id: Number(e.target.value) } : l) })}>{c.parent_ids.map(id => { const parent = data.lots.find(l => l.id === id); return <option key={id} value={id}>#{id} · {parent.label} · {parent.date}</option> })}</select></td>
            </tr> })}</tbody></table></div>
          <details><summary>Verify lots that were separate purchases</summary>
            <p>Select only lots supported by separate purchase records. A card can have both purchased and transferred lots.</p>
            {data.lots.filter(l => issue.acquisition_ids?.includes(l.id) && !form.links.some(link => link.lot_id === l.id)).map(l => <label key={l.id} style={{ display: 'block' }}>
              <input type="checkbox" checked={form.purchase_ids.includes(l.id)} onChange={e => update({ purchase_ids: e.target.checked ? [...form.purchase_ids, l.id] : form.purchase_ids.filter(id => id !== l.id) })} />
              #{l.id} · {l.label} · {l.date} · {l.quantity} @ ${l.unit_cost} — verified separate purchase
            </label>)}
          </details>
        </>}
        {form.action === 'archive' && <Field label="Disposition"><select value={form.restore ? 'restore' : 'dispose'} onChange={e => update({ restore: e.target.value === 'restore' })}><option value="restore">Restore this record to active stock</option><option value="dispose">Remove these units and their remaining cost (non-sale)</option></select></Field>}
        {form.action === 'classify_outflow' && <>
          {data.outflows.filter(a => issue.consumption_ids?.includes(a.id)).map(a => <label key={a.id} style={{ display: 'block' }}><input type="checkbox" checked={form.consumption_ids.includes(a.id)} onChange={e => update({ consumption_ids: e.target.checked ? [...form.consumption_ids, a.id] : form.consumption_ids.filter(id => id !== a.id) })} /> Outflow #{a.id} · Lot #{a.lot_id} · {a.quantity} units @ ${a.unit_cost} · {a.date.slice(0, 10)}</label>)}
          <Field label="What happened?"><select value={form.kind} onChange={e => update({ kind: e.target.value })}><option value="adjustment">Non-sale stock correction / loss</option><option value="supplier_return">Supplier return</option><option value="sale_unlinked">A sale with a missing order record (keeps a review flag)</option></select></Field>
        </>}
        {form.action === 'journal_opening' && <p>Use this only when the current physical quantity is confirmed and its opening/history entry was missing. Stock and cost lots remain unchanged.</p>}
        <Field label="Explanation / supporting record"><textarea rows="3" style={{ width: '100%', minWidth: 260 }} value={form.reason} onChange={e => update({ reason: e.target.value })} placeholder="What happened, and what evidence supports this correction?" /></Field>
        <button onClick={preview} disabled={form.reason.trim().length < 5}>Preview repair</button>
      </fieldset>
      {ticket && <div className="panel" style={{ marginTop: 16 }}>
        <h4>Review the effect on overall totals</h4>
        <table><thead><tr><th>Total</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody>{Object.entries(METRICS).map(([key, label]) => {
          const monetary = !key.endsWith('units'), display = v => monetary ? `$${Number(v).toFixed(4)}` : v
          return <tr key={key}><td>{label}</td><td>{display(ticket.before[key])}</td><td>{display(ticket.after[key])}</td><td>{display(ticket.after[key] - ticket.before[key])}</td></tr>
        })}</tbody></table>
        <details><summary>{ticket.changes.length} record changes — show details</summary>
          <table><thead><tr><th>Record</th><th>Field</th><th>Before</th><th>After</th></tr></thead><tbody>{ticket.changes.flatMap(c => Object.entries(FIELDS).filter(([field]) => (c.before?.[field] ?? null) !== (c.after?.[field] ?? null)).map(([field, label]) => <tr key={`${c.table}-${c.id}-${field}`}><td>{TABLES[c.table]} #{c.id}</td><td>{label}</td><td>{String(c.before?.[field] ?? '—')}</td><td>{String(c.after?.[field] ?? '—')}</td></tr>))}</tbody></table>
        </details>
        <p>Reason: {ticket.payload.reason}</p>
        <button className="primary" disabled={busy} onClick={apply}>{busy ? 'Applying…' : 'Apply this reviewed repair'}</button>
      </div>}
    </>}
  </Modal>
}

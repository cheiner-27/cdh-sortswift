import React, { useEffect, useState } from 'react'
import { api, fmtMoney } from '../api.js'
import { DropdownWithAdd, Field, Modal, Msg, SortTh, useMsg, useSort } from '../components.jsx'

const today = () => new Date().toISOString().slice(0, 10)

// Total FIFO cost booked against an order (0 until the sale is deducted).
const orderCogs = (o) => o.items.reduce((s, i) => s + (i.cogs || 0), 0)
// Net profit = revenue + shipping charged − refunds − COGS − shipping paid − fees
// you actually ate (fees credited back on a refund aren't a cost).
const orderNet = (o) => o.order_total + (o.shipping_charged || 0) - (o.amount_refunded || 0)
  - orderCogs(o) - (o.shipping_cost || 0) - (o.return_shipping_cost || 0)
  - ((o.marketplace_fees || 0) - (o.fees_refunded || 0))
const money = (n) => ({ color: n >= 0 ? 'var(--green)' : 'var(--red)' })
// Destination, not buyer: names aren't retained on orders. eBay and the packing
// slip intake spell the address fields differently, so accept both.
const shipTo = (o) => {
  const s = o.ship_to || {}
  const city = s.city || ''
  const state = s.state || s.stateOrProvince || ''
  return [city, state].filter(Boolean).join(', ')
}

export default function OrdersPage() {
  const [msg, ok, err] = useMsg()
  const [orders, setOrders] = useState([])
  const { sorted, sort, toggle: sortBy } = useSort(orders)
  const [status, setStatus] = useState('')
  const [selected, setSelected] = useState(new Set())
  const [pickList, setPickList] = useState(null)
  const [slip, setSlip] = useState(null)
  const [shipFor, setShipFor] = useState(null)
  const [manualOpen, setManualOpen] = useState(false)
  const [refundFor, setRefundFor] = useState(null)
  const [costsFor, setCostsFor] = useState(null)

  const refresh = () =>
    api.get(`/api/orders${status ? `?status=${status}` : ''}`).then(setOrders)
  useEffect(() => { refresh() }, [status])

  const toggle = (id) => setSelected((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
  })

  const makePickList = async () => {
    try {
      const r = await api.post('/api/orders/pick-list', { order_ids: [...selected] })
      setPickList(r)
      setTimeout(() => window.print(), 300)
    } catch (e) { err(e) }
  }

  const printSlip = async (o) => {
    setSlip(await api.get(`/api/orders/${o.id}/packing-slip`))
    setTimeout(() => window.print(), 300)
  }

  const buyLabel = async (o) => {
    try {
      const r = await api.post(`/api/orders/${o.id}/buy-label`, {})
      ok(`Label bought: ${r.tracking_number} (${fmtMoney(r.cost)})`)
      if (r.label_url) window.open(r.label_url, '_blank')
      refresh()
    } catch (e) { err(e) }
  }

  const deleteOrder = async (o) => {
    if (!window.confirm(`Delete order ${o.external_order_id}? This removes the order record only — inventory is NOT restocked.`)) return
    try {
      await api.del(`/api/orders/${o.id}`)
      ok('Order deleted (inventory unchanged)')
      refresh()
    } catch (e) { err(e) }
  }

  const bulkShip = async () => {
    const targets = orders.filter((o) => selected.has(o.id) && o.status === 'open')
    if (!targets.length) { err({ message: 'No open orders selected' }); return }
    if (!window.confirm(`Mark ${targets.length} order(s) shipped and deduct their stock?\n\nNo tracking is sent to the marketplace — use each order's Ship button if you need that.`)) return
    let warnings = 0
    for (const o of targets) {
      try { const r = await api.post(`/api/orders/${o.id}/mark-shipped`, {}); warnings += r.warnings?.length || 0 }
      catch { warnings++ }
    }
    ok(`Marked ${targets.length} shipped${warnings ? ` — ${warnings} warning(s)` : ''}`)
    setSelected(new Set()); refresh()
  }

  const bulkDelete = async () => {
    const targets = orders.filter((o) => selected.has(o.id))
    if (!targets.length) return
    if (!window.confirm(`Delete ${targets.length} order(s)? This removes the order records only — inventory is NOT restocked.`)) return
    for (const o of targets) { try { await api.del(`/api/orders/${o.id}`) } catch { /* skip */ } }
    ok(`Deleted ${targets.length} order(s)`)
    setSelected(new Set()); refresh()
  }

  return (
    <div>
      <h2>Orders</h2>
      <div className="row center">
        <Field label="Status"><select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">all</option><option>open</option><option>shipped</option>
          <option value="partially_refunded">partially refunded</option>
          <option>cancelled</option><option>refunded</option></select></Field>
        <button disabled={!selected.size} className="primary" onClick={makePickList}>
          Pick list ({selected.size})</button>
        <button disabled={!selected.size} onClick={bulkShip}
          title="Mark selected open orders shipped and deduct stock (no marketplace tracking)">
          Mark shipped ({selected.size})</button>
        <button disabled={!selected.size} className="danger" onClick={bulkDelete}
          title="Delete selected order records (inventory is not restocked)">
          Delete ({selected.size})</button>
        <button onClick={() => setManualOpen(true)}>+ Manual sale</button>
        <Msg msg={msg} />
      </div>

      <div className="panel table-wrap"><table>
        <thead><tr>
          <th><input type="checkbox" title="select all"
            checked={sorted.length > 0 && sorted.every((o) => selected.has(o.id))}
            onChange={(e) => setSelected(e.target.checked ? new Set(sorted.map((o) => o.id)) : new Set())} /></th>
          <SortTh k="order" accessor={(o) => o.ordered_at} sort={sort} toggle={sortBy}>Order</SortTh>
          <SortTh k="shipto" accessor={(o) => shipTo(o)} sort={sort} toggle={sortBy}>Ship to</SortTh>
          <th>Items</th>
          <SortTh k="total" accessor={(o) => o.order_total} sort={sort} toggle={sortBy}>Total</SortTh>
          <SortTh k="profit" accessor={(o) => orderNet(o)} sort={sort} toggle={sortBy}>Profit</SortTh>
          <SortTh k="status" accessor={(o) => o.status} sort={sort} toggle={sortBy}>Status</SortTh>
          <SortTh k="tracking" accessor={(o) => o.tracking_number} sort={sort} toggle={sortBy}>Tracking</SortTh>
          <th></th></tr></thead>
        <tbody>{sorted.map((o) => (
          <tr key={o.id}>
            <td><input type="checkbox" checked={selected.has(o.id)} onChange={() => toggle(o.id)} /></td>
            <td>
              <span className="badge blue">{o.marketplace}</span> {o.external_order_id}
              {o.is_direct && <span className="badge yellow">Direct</span>}
              <div className="muted">{o.ordered_at?.slice(0, 16).replace('T', ' ')}</div>
            </td>
            <td>{shipTo(o)}</td>
            <td>{o.items.map((i) => {
              const margin = i.unit_price * i.quantity - (i.cogs || 0)
              return (
                <div key={i.id} className="muted" style={{ fontSize: 12 }}>{i.quantity}× {i.description}
                  {o.deduction_applied && <span style={money(margin)}> · {fmtMoney(margin)}</span>}
                </div>)
            })}</td>
            <td>{fmtMoney(o.order_total)}
              {o.shipping_charged > 0 && <div style={{ color: 'var(--green)' }}>+ship chgd {fmtMoney(o.shipping_charged)}</div>}
              {o.shipping_cost > 0 && <div className="muted">ship {fmtMoney(o.shipping_cost)}</div>}
              {o.marketplace_fees > 0 && <div className="muted">fees {fmtMoney(o.marketplace_fees)}</div>}
              {o.amount_refunded > 0 && <div style={{ color: 'var(--red)' }}>refunded {fmtMoney(o.amount_refunded)}</div>}
              {o.fees_refunded > 0 && <div style={{ color: 'var(--green)' }}>fees back {fmtMoney(o.fees_refunded)}</div>}
              {o.return_shipping_cost > 0 && <div className="muted">ret. ship {fmtMoney(o.return_shipping_cost)}</div>}</td>
            <td><b style={money(orderNet(o))}>{fmtMoney(orderNet(o))}</b>
              {o.deduction_applied
                ? <div className="muted" style={{ fontSize: 11 }}>COGS {fmtMoney(orderCogs(o))}</div>
                : <div className="muted" style={{ fontSize: 11 }} title="Profit excludes COGS until the sale is deducted (on ship)">COGS pending</div>}</td>
            <td><span className={`badge ${o.status === 'shipped' ? 'green' : o.status === 'open' ? 'blue' : o.status === 'partially_refunded' ? 'yellow' : ''}`}>{o.status.replace('_', ' ')}</span></td>
            <td className="muted">{o.tracking_number || '—'}</td>
            <td>
              <button className="small" onClick={() => printSlip(o)}>Slip</button>{' '}
              <button className="small" onClick={() => setCostsFor(o)}>Costs</button>{' '}
              {o.status === 'open' && (<>
                <button className="small" onClick={() => buyLabel(o)}>Label</button>{' '}
                <button className="small primary" onClick={() => setShipFor(o)}>Ship</button>{' '}
                <button className="small danger" onClick={async () => {
                  try { await api.post(`/api/orders/${o.id}/cancel`); refresh() } catch (e) { err(e) }
                }}>Cancel</button>
              </>)}
              {(o.status === 'shipped' || o.status === 'partially_refunded') &&
                <button className="small danger" onClick={() => setRefundFor(o)}>Refund…</button>}
              {' '}<button className="small danger" title="Removes the order record only — inventory is NOT restocked"
                onClick={() => deleteOrder(o)}>Delete (no restock)</button>
            </td>
          </tr>))}</tbody>
      </table>
        {orders.length === 0 && <p className="muted">No orders. eBay orders arrive via polling or "Sync orders now" on the Marketplaces page; TCGplayer sales arrive via Deduction CSV import.</p>}
      </div>

      {shipFor && <ShipModal order={shipFor} onClose={() => { setShipFor(null); refresh() }} />}
      {manualOpen && <ManualSaleModal onClose={() => { setManualOpen(false); refresh() }} />}
      {refundFor && <RefundModal order={refundFor} onClose={() => { setRefundFor(null); refresh() }} />}
      {costsFor && <CostsModal order={costsFor} onClose={() => { setCostsFor(null); refresh() }} />}

      {pickList && (
        <div className="print-area">
          <h2>Pick List</h2>
          <table><thead><tr><th>Bin</th><th>Card</th><th>Set</th><th>#</th><th>Cond</th>
            <th>Printing</th><th>Qty</th><th>SKU</th><th>Mkt ID</th><th>Orders</th></tr></thead>
            <tbody>{pickList.rows.map((r, i) => (
              <tr key={i}>
                <td><b>{r.bin}</b></td>
                <td>{r.name}{r.is_direct ? ' [DIRECT]' : ''}</td>
                <td>{r.set_code}</td><td>{r.collector_number}</td>
                <td>{r.condition}</td><td>{r.printing}</td>
                <td>{r.quantity}</td><td>{r.sku}</td>
                <td>{r.marketplace_product_id}</td>
                <td>{r.orders.join(', ')}</td>
              </tr>))}</tbody></table>
        </div>
      )}

      {slip && (
        <div className="print-area">
          <h2>Packing Slip</h2>
          <p>Order: {slip.order_number} ({slip.marketplace})<br />
            Date: {slip.ordered_at?.slice(0, 10)}<br />
            Ship to: {slip.ship_to?.addressLine1 || slip.ship_to?.address_line1 || ''}{' '}
            {slip.ship_to?.city || ''}{' '}
            {slip.ship_to?.state || slip.ship_to?.stateOrProvince || ''}{' '}
            {slip.ship_to?.zip || slip.ship_to?.postalCode || ''}</p>
          <table><thead><tr><th>Item</th><th>Qty</th><th>Price</th><th>Total</th></tr></thead>
            <tbody>{slip.items.map((i, idx) => (
              <tr key={idx}><td>{i.description}</td><td>{i.quantity}</td>
                <td>{fmtMoney(i.unit_price)}</td><td>{fmtMoney(i.total)}</td></tr>))}</tbody></table>
          <p><b>Order total: {fmtMoney(slip.order_total)}</b></p>
          <p>Thank you for your purchase!</p>
        </div>
      )}
    </div>
  )
}

function ShipModal({ order, onClose }) {
  const [msg, ok, err] = useMsg()
  const [tracking, setTracking] = useState(order.tracking_number || '')
  const [carrier, setCarrier] = useState(order.carrier || 'USPS')
  const ship = async () => {
    try {
      const r = await api.post(`/api/orders/${order.id}/mark-shipped`, {
        tracking_number: tracking || undefined, carrier,
      })
      if (r.warnings.length) {
        // Keep the dialog open so the warning stays visible.
        ok(`Marked shipped — ${r.warnings.join('; ')}`)
      } else {
        ok('Marked shipped')
        setTimeout(onClose, 500)
      }
    } catch (e) { err(e) }
  }
  return (
    <Modal title={`Ship order ${order.external_order_id}`} onClose={onClose}>
      <p className="muted">Enter tracking manually if postage was bought outside the app; the marketplace mark-shipped call still fires.</p>
      <div className="row">
        <Field label="Tracking number"><input style={{ width: 240 }} value={tracking}
          onChange={(e) => setTracking(e.target.value)} /></Field>
        <Field label="Carrier"><select value={carrier} onChange={(e) => setCarrier(e.target.value)}>
          <option>USPS</option><option>UPS</option><option>FedEx</option><option>Other</option></select></Field>
        <button className="primary" onClick={ship}>Mark shipped</button>
      </div>
      <Msg msg={msg} />
    </Modal>
  )
}

function ManualSaleModal({ onClose }) {
  const [msg, ok, err] = useMsg()
  const [platform, setPlatform] = useState('')
  const [platforms, setPlatforms] = useState([])
  const [items, setItems] = useState([])
  const [q, setQ] = useState('')
  const [found, setFound] = useState([])
  const [shipping, setShipping] = useState('')
  const [shippingCharged, setShippingCharged] = useState('')
  const [fees, setFees] = useState('')
  const [saleDate, setSaleDate] = useState(today())

  useEffect(() => {
    api.get('/api/orders/platforms').then((r) => setPlatforms(r.platforms || [])).catch(() => {})
  }, [])

  const search = async () => {
    if (!q.trim()) return
    try {
      const r = await api.post('/api/inventory/search', { q, in_stock_only: true, limit: 25 })
      setFound(r.items)
    } catch (e) { err(e) }
  }
  const label = (it) => it.card
    ? `${it.card.name} [${it.card.set_code} #${it.card.collector_number}] ${it.condition} ${it.printing}`
    : `${it.custom_name || 'item'} #${it.id}`
  const addLine = (it) => setItems((xs) => xs.some((x) => x.inventory_id === it.id) ? xs : [...xs, {
    inventory_id: it.id, label: label(it), on_hand: it.quantity,
    quantity: 1, unit_price: it.price_override ?? it.current_price ?? '',
  }])
  const setLine = (i, k, v) => setItems((xs) => xs.map((x, j) => (j === i ? { ...x, [k]: v } : x)))

  const submit = async () => {
    try {
      await api.post('/api/orders/manual', {
        platform: platform || 'manual',
        ordered_at: saleDate || undefined,
        shipping_cost: shipping === '' ? 0 : Number(shipping),
        shipping_charged: shippingCharged === '' ? 0 : Number(shippingCharged),
        marketplace_fees: fees === '' ? 0 : Number(fees),
        items: items.map((i) => ({
          inventory_id: i.inventory_id,
          quantity: Number(i.quantity) || 1,
          unit_price: Number(i.unit_price) || 0,
        })),
      })
      ok('Manual sale recorded (inventory deducted)')
      setItems([])
    } catch (e) { err(e) }
  }
  return (
    <Modal title="Manual / offline sale" onClose={onClose} wide>
      <div className="row">
        <Field label="Platform (where it sold)">
          <DropdownWithAdd value={platform} onChange={setPlatform} options={platforms}
            width={200} placeholder="select platform…" />
        </Field>
        <Field label="Sale date"><input type="date" value={saleDate}
          onChange={(e) => setSaleDate(e.target.value)} /></Field>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        Records a manually-entered sale and deducts inventory. Pick the platform it sold on
        (add your own with "Add new"). Search your live inventory below and add the cards sold.
      </p>
      <div className="row center">
        <input placeholder="search inventory (card name / comment)" style={{ width: 260 }}
          value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search()} />
        <button onClick={search}>Search inventory</button>
      </div>
      {found.length > 0 && (
        <div className="table-wrap" style={{ maxHeight: 200, overflowY: 'auto' }}>
          <table><thead><tr><th>Item</th><th>Bin</th><th>Qty</th><th>Price</th><th></th></tr></thead>
            <tbody>{found.map((it) => (
              <tr key={it.id}>
                <td>{label(it)}</td>
                <td className="muted">{it.bin || '—'}</td>
                <td>{it.quantity}</td>
                <td>{fmtMoney(it.price_override ?? it.current_price)}</td>
                <td><button className="small primary" onClick={() => addLine(it)}>Add</button></td>
              </tr>))}</tbody></table>
        </div>)}

      {items.length > 0 && (
        <table><thead><tr><th>Sold item</th><th>Qty</th><th>Unit price</th><th></th></tr></thead>
          <tbody>{items.map((it, i) => (
            <tr key={it.inventory_id}>
              <td>{it.label} <span className="muted">({it.on_hand} on hand)</span></td>
              <td><input type="number" min="1" style={{ width: 55 }} value={it.quantity}
                onChange={(e) => setLine(i, 'quantity', e.target.value)} /></td>
              <td><input style={{ width: 70 }} value={it.unit_price}
                onChange={(e) => setLine(i, 'unit_price', e.target.value)} /></td>
              <td><button className="small danger" onClick={() => setItems((xs) => xs.filter((_, j) => j !== i))}>✕</button></td>
            </tr>))}</tbody></table>)}

      <div className="row">
        <Field label="Shipping charged buyer ($, revenue)"><input style={{ width: 130 }} value={shippingCharged}
          onChange={(e) => setShippingCharged(e.target.value)} placeholder="0 = free ship" /></Field>
        <Field label="Shipping you paid ($, cost)"><input style={{ width: 120 }} value={shipping}
          onChange={(e) => setShipping(e.target.value)} /></Field>
        <Field label="Fees ($)"><input style={{ width: 90 }} value={fees}
          onChange={(e) => setFees(e.target.value)} /></Field>
      </div>
      <div className="row center">
        <button className="primary" disabled={!items.length} onClick={submit}>Record sale</button>
        <Msg msg={msg} />
      </div>
    </Modal>
  )
}

function RefundModal({ order, onClose }) {
  const [msg, ok, err] = useMsg()
  const [mode, setMode] = useState('full')
  const [amount, setAmount] = useState('')
  const [returned, setReturned] = useState(true)
  const [returnShipping, setReturnShipping] = useState('')
  const fees = order.marketplace_fees || 0
  // TCGplayer credits selling fees back on a refund, so default to the full fee.
  const [feesRefunded, setFeesRefunded] = useState(fees ? fees.toFixed(2) : '')
  const alreadyRefunded = order.amount_refunded || 0
  // Shipping the buyer paid is revenue too, so it's refundable alongside the items.
  const refundable = order.order_total + (order.shipping_charged || 0)
  const remaining = (refundable - alreadyRefunded).toFixed(2)
  // Lines with no inventory record behind them (migrated sales) can't be restocked.
  const unlinked = order.items.filter((i) => !i.inventory_id).length

  const submit = async () => {
    try {
      const body = mode === 'partial'
        ? { mode: 'partial', amount: Number(amount) }
        : {
          mode: 'full', returned, return_shipping: Number(returnShipping) || 0,
          fees_refunded: Number(feesRefunded) || 0,
        }
      const r = await api.post(`/api/orders/${order.id}/refund`, body)
      const stranded = (r.unlinked_lines || []).length
      ok(mode === 'partial'
        ? `Partial refund recorded (total refunded ${fmtMoney(r.amount_refunded)})`
        : `Full refund — ${r.restocked ? 'item returned, COGS backed out' : 'item NOT returned, written off'}`
          + (stranded ? ` · ${stranded} line(s) had no inventory record — re-add that stock by hand to re-list it` : ''))
      setTimeout(onClose, stranded ? 2500 : 700)
    } catch (e) { err(e) }
  }
  return (
    <Modal title={`Refund — order ${order.external_order_id}`} onClose={onClose}>
      <p className="muted" style={{ marginTop: 0 }}>
        Refundable {fmtMoney(refundable)} (items {fmtMoney(order.order_total)}
        {order.shipping_charged > 0 && <> + shipping charged {fmtMoney(order.shipping_charged)}</>})
        {alreadyRefunded > 0 && <> · already refunded {fmtMoney(alreadyRefunded)} · {fmtMoney(remaining)} left</>}
      </p>
      <div className="row center">
        <Field label="Refund type"><select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="full">full refund</option>
          <option value="partial">partial refund</option></select></Field>
      </div>
      {mode === 'partial' ? (
        <div className="row center">
          <Field label={`Amount to refund the buyer ($, ≤ ${remaining})`}>
            <input style={{ width: 90 }} value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
          <span className="muted">Reduces net revenue and credits back a pro-rata
            slice of the selling fees; inventory and COGS unchanged.</span>
        </div>
      ) : (
        <>
          <div className="row center">
            <label><input type="checkbox" checked={returned} onChange={(e) => setReturned(e.target.checked)} /> item was returned to me</label>
          </div>
          <p className="muted" style={{ fontSize: 12 }}>
            {returned
              ? 'Backs the COGS out of this sale so the cost follows the card to whichever sale sticks — the refunded sale is left showing only the shipping you ate.'
              : 'Write-off: the card does NOT come back; inventory stays deducted and its cost remains a loss.'}
          </p>
          {returned && unlinked > 0 && (
            <p className="muted" style={{ fontSize: 12, color: 'var(--yellow)' }}>
              {unlinked} line(s) on this order have no inventory record (migrated sale) — the
              COGS is backed out, but the card is NOT auto-restocked. Re-add that stock on the
              Inventory page before re-listing it.
            </p>)}
          {returned && (
            <div className="row center">
              <Field label="Return shipping you paid ($)"><input style={{ width: 90 }}
                value={returnShipping} onChange={(e) => setReturnShipping(e.target.value)} /></Field>
            </div>)}
          <div className="row center">
            <Field label={`Selling fees credited back ($ of ${fmtMoney(fees)} charged)`}>
              <input style={{ width: 90 }} value={feesRefunded}
                onChange={(e) => setFeesRefunded(e.target.value)} /></Field>
            <span className="muted" style={{ fontSize: 12 }}>
              TCGplayer refunds these in full; set to 0 if the marketplace kept them.</span>
          </div>
        </>
      )}
      <div className="row center">
        <button className="primary danger" onClick={submit}>Record refund</button>
        <Msg msg={msg} />
      </div>
    </Modal>
  )
}

function CostsModal({ order, onClose }) {
  const [msg, ok, err] = useMsg()
  const [shipping, setShipping] = useState(order.shipping_cost ?? '')
  const [shippingCharged, setShippingCharged] = useState(order.shipping_charged ?? '')
  const [fees, setFees] = useState(order.marketplace_fees ?? '')
  const [orderedAt, setOrderedAt] = useState(order.ordered_at?.slice(0, 10) || '')
  const save = async () => {
    try {
      await api.patch(`/api/orders/${order.id}/costs`, {
        shipping_cost: shipping === '' ? undefined : Number(shipping),
        shipping_charged: shippingCharged === '' ? undefined : Number(shippingCharged),
        marketplace_fees: fees === '' ? undefined : Number(fees),
        ordered_at: orderedAt || undefined,
      })
      ok('Order updated'); setTimeout(onClose, 500)
    } catch (e) { err(e) }
  }
  return (
    <Modal title={`Costs & date — order ${order.external_order_id}`} onClose={onClose}>
      <p className="muted" style={{ marginTop: 0 }}>Record what you actually paid — e.g. postage bought outside the app, or fees the sync didn't capture — plus any shipping the buyer paid you (revenue). Adjust the sale date if you recorded it late. Feeds directly into Reports → P&L.</p>
      <div className="row">
        <Field label="Sale date"><input type="date" value={orderedAt}
          onChange={(e) => setOrderedAt(e.target.value)} /></Field>
        <Field label="Shipping charged buyer ($, revenue)"><input style={{ width: 130 }} value={shippingCharged}
          onChange={(e) => setShippingCharged(e.target.value)} /></Field>
        <Field label="Shipping you paid ($, cost)"><input style={{ width: 120 }} value={shipping}
          onChange={(e) => setShipping(e.target.value)} /></Field>
        <Field label="Marketplace / processing fees ($)"><input style={{ width: 90 }} value={fees}
          onChange={(e) => setFees(e.target.value)} /></Field>
      </div>
      <div className="row center">
        <button className="primary" onClick={save}>Save costs</button>
        <Msg msg={msg} />
      </div>
    </Modal>
  )
}

import React, { useEffect, useState } from 'react'
import { api, fmtMoney } from '../api.js'
import { DropdownWithAdd, Field, Modal, Msg, useMeta, useMsg } from '../components.jsx'

// Bulk piles: opaque card lots bought and sold by count, never inventoried
// per-card. Buy → FIFO cost batch; sell → manual order (FIFO COGS); pull good
// cards out via the Scan page's "Pull from bulk lot" selector.
export default function BulkPage() {
  const meta = useMeta()
  const [msg, ok, err] = useMsg()
  const [piles, setPiles] = useState([])
  const [creating, setCreating] = useState(false)
  const [buyPile, setBuyPile] = useState(null)
  const [sellPile, setSellPile] = useState(null)

  const refresh = () => api.get('/api/bulk/piles').then(setPiles)
  useEffect(() => { refresh() }, [])

  const deletePile = async (p) => {
    const warn = p.on_hand
      ? `Delete "${p.name}"? Its ${p.on_hand.toLocaleString()} on-hand card(s) are hidden from inventory and valuation (sales history is kept).`
      : `Delete "${p.name}"?`
    if (!window.confirm(warn)) return
    try { await api.del(`/api/bulk/piles/${p.id}`); ok('Pile deleted'); refresh() } catch (e) { err(e) }
  }

  if (!meta) return null
  return (
    <div>
      <h2>Bulk <span className="muted">(uninventoried card lots)</span></h2>
      <div className="panel">
        <p className="muted" style={{ marginTop: 0, maxWidth: 900 }}>
          A <b>bulk pile</b> is cards you buy and sell by the count without tracking each one —
          commons, lands, energy, "500 assorted." Record what you <b>buy</b> (cost is kept FIFO, so
          the first cards sold cost what the first batch cost); sell it in chunks (a "pack of 100");
          and <b>pull</b> the good cards out into real inventory from the <b>Scan</b> page by picking
          this pile as the source — that decrements the pile and carries its per-card cost onto the card.
        </p>
        <button className="primary" onClick={() => setCreating(true)}>+ New bulk pile</button>
        <Msg msg={msg} />
      </div>

      <div className="panel table-wrap"><table>
        <thead><tr>
          <th>Pile</th><th>Game</th><th>On hand</th><th>Cost basis</th>
          <th>Avg $/card</th><th>Next $/card (FIFO)</th><th>Sell price</th><th></th>
        </tr></thead>
        <tbody>{piles.map((p) => (
          <tr key={p.id}>
            <td><b>{p.name}</b>{p.group && <span className="muted"> · {p.group}</span>}
              {p.description && <div className="muted" style={{ fontSize: 12 }}>{p.description}</div>}</td>
            <td>{p.game}</td>
            <td><b>{p.on_hand.toLocaleString()}</b> cards</td>
            <td>{fmtMoney(p.cost_basis)}</td>
            <td>{p.avg_unit_cost == null ? '—' : `$${p.avg_unit_cost.toFixed(4)}`}</td>
            <td title="Cost of the next cards to sell (oldest batch)">
              {p.next_unit_cost == null ? '—' : `$${p.next_unit_cost.toFixed(4)}`}</td>
            <td>{fmtMoney(p.current_price)}</td>
            <td className="row" style={{ marginBottom: 0 }}>
              <button className="small primary" onClick={() => setBuyPile(p)}>Buy</button>
              <button className="small" disabled={!p.on_hand} onClick={() => setSellPile(p)}>Sell</button>
              <button className="small danger" onClick={() => deletePile(p)}>Delete</button>
            </td>
          </tr>))}
        </tbody>
      </table>
        {piles.length === 0 && <p className="muted">No bulk piles yet. Create one, then record a purchase into it.</p>}
      </div>

      {creating && <NewPile meta={meta} onClose={() => setCreating(false)}
        onDone={() => { setCreating(false); refresh() }} />}
      {buyPile && <BuyModal pile={buyPile} onClose={() => setBuyPile(null)}
        onDone={() => { setBuyPile(null); refresh() }} />}
      {sellPile && <SellModal pile={sellPile} onClose={() => setSellPile(null)}
        onDone={() => { setSellPile(null); refresh() }} />}
    </div>
  )
}

function NewPile({ meta, onClose, onDone }) {
  const [msg, ok, err] = useMsg()
  const [f, setF] = useState({ name: '', game: (meta.custom_categories || [])[0] || 'Other', group: '', description: '' })
  const submit = async () => {
    try {
      await api.post('/api/bulk/piles', f)
      onDone()
    } catch (e) { err(e) }
  }
  return (
    <Modal title="New bulk pile" onClose={onClose}>
      <div className="row">
        <Field label="Name"><input style={{ width: 240 }} autoFocus value={f.name}
          placeholder="MTG Bulk Commons" onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Game / category"><select value={f.game}
          onChange={(e) => setF({ ...f, game: e.target.value })}>
          {(meta.custom_categories || []).map((c) => <option key={c}>{c}</option>)}</select></Field>
      </div>
      <div className="row">
        <Field label="Group (optional)"><input style={{ width: 160 }} value={f.group}
          placeholder="commons, lands…" onChange={(e) => setF({ ...f, group: e.target.value })} /></Field>
        <Field label="Description (optional)"><input style={{ width: 260 }} value={f.description}
          onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
      </div>
      <div className="row center">
        <button className="primary" disabled={!f.name.trim()} onClick={submit}>Create</button>
        <Msg msg={msg} />
      </div>
    </Modal>
  )
}

function BuyModal({ pile, onClose, onDone }) {
  const [msg, ok, err] = useMsg()
  const [f, setF] = useState({ quantity: '', total_cost: '', unit_cost: '', bin: '', price: '', acquired_at: '' })
  const qty = Number(f.quantity) || 0
  const perCard = f.unit_cost !== '' ? Number(f.unit_cost)
    : (f.total_cost !== '' && qty ? Number(f.total_cost) / qty : null)
  const submit = async () => {
    try {
      await api.post(`/api/bulk/piles/${pile.id}/purchase`, {
        quantity: qty,
        total_cost: f.total_cost === '' ? null : Number(f.total_cost),
        unit_cost: f.unit_cost === '' ? null : Number(f.unit_cost),
        bin: f.bin || '',
        price: f.price === '' ? null : Number(f.price),
        acquired_at: f.acquired_at || null,
      })
      onDone()
    } catch (e) { err(e) }
  }
  return (
    <Modal title={`Buy into ${pile.name}`} onClose={onClose}>
      <p className="muted" style={{ marginTop: 0 }}>Records a FIFO cost batch. Enter the total you paid
        <i> or</i> a per-card cost — not both.</p>
      <div className="row">
        <Field label="Cards bought"><input style={{ width: 100 }} autoFocus value={f.quantity}
          onChange={(e) => setF({ ...f, quantity: e.target.value })} /></Field>
        <Field label="Total paid ($)"><input style={{ width: 90 }} value={f.total_cost}
          disabled={f.unit_cost !== ''} onChange={(e) => setF({ ...f, total_cost: e.target.value })} /></Field>
        <Field label="…or per-card ($)"><input style={{ width: 90 }} value={f.unit_cost}
          disabled={f.total_cost !== ''} onChange={(e) => setF({ ...f, unit_cost: e.target.value })} /></Field>
      </div>
      <div className="row">
        <Field label="Bin (optional)"><input style={{ width: 100 }} value={f.bin}
          onChange={(e) => setF({ ...f, bin: e.target.value })} /></Field>
        <Field label="Sell price (optional)"><input style={{ width: 90 }} value={f.price}
          onChange={(e) => setF({ ...f, price: e.target.value })} /></Field>
        <Field label="Acquired date"><input type="date" style={{ width: 140 }} value={f.acquired_at}
          onChange={(e) => setF({ ...f, acquired_at: e.target.value })} /></Field>
      </div>
      {perCard != null && qty > 0 &&
        <p className="muted">= <b>${perCard.toFixed(4)}</b> / card × {qty.toLocaleString()} cards</p>}
      <div className="row center">
        <button className="primary" disabled={!qty} onClick={submit}>Record purchase</button>
        <Msg msg={msg} />
      </div>
    </Modal>
  )
}

function SellModal({ pile, onClose, onDone }) {
  const [msg, ok, err] = useMsg()
  const [platform, setPlatform] = useState('')
  const [platforms, setPlatforms] = useState([])
  const [f, setF] = useState({ quantity: '', total_price: '',
    marketplace_fees: '', shipping_cost: '', shipping_charged: '', ordered_at: '' })
  const qty = Number(f.quantity) || 0
  useEffect(() => {
    api.get('/api/orders/platforms').then((r) => setPlatforms(r.platforms || [])).catch(() => {})
  }, [])
  const submit = async () => {
    try {
      const res = await api.post(`/api/bulk/piles/${pile.id}/sell`, {
        quantity: qty,
        total_price: f.total_price === '' ? null : Number(f.total_price),
        buyer_name: platform || '',
        marketplace_fees: f.marketplace_fees === '' ? 0 : Number(f.marketplace_fees),
        shipping_cost: f.shipping_cost === '' ? 0 : Number(f.shipping_cost),
        shipping_charged: f.shipping_charged === '' ? 0 : Number(f.shipping_charged),
        ordered_at: f.ordered_at || null,
      })
      ok(`Recorded sale (order #${res.order_id})`)
      setTimeout(onDone, 600)
    } catch (e) { err(e) }
  }
  return (
    <Modal title={`Sell from ${pile.name}`} onClose={onClose}>
      <p className="muted" style={{ marginTop: 0 }}>Records a manual sale by card count — COGS is booked
        FIFO (oldest cards first) and it lands in Orders and the P&amp;L report. {pile.on_hand.toLocaleString()} cards on hand.</p>
      <div className="row">
        <Field label="Cards sold"><input style={{ width: 100 }} autoFocus value={f.quantity}
          onChange={(e) => setF({ ...f, quantity: e.target.value })} /></Field>
        <Field label="Sale price ($ total)"><input style={{ width: 100 }} value={f.total_price}
          onChange={(e) => setF({ ...f, total_price: e.target.value })} /></Field>
        <Field label="Platform (where it sold)">
          <DropdownWithAdd value={platform} onChange={setPlatform} options={platforms}
            width={160} placeholder="select platform…" /></Field>
      </div>
      <div className="row">
        <Field label="Fees ($)"><input style={{ width: 80 }} value={f.marketplace_fees}
          onChange={(e) => setF({ ...f, marketplace_fees: e.target.value })} /></Field>
        <Field label="Shipping cost ($)"><input style={{ width: 90 }} value={f.shipping_cost}
          onChange={(e) => setF({ ...f, shipping_cost: e.target.value })} /></Field>
        <Field label="Shipping charged ($)"><input style={{ width: 90 }} value={f.shipping_charged}
          onChange={(e) => setF({ ...f, shipping_charged: e.target.value })} /></Field>
        <Field label="Sale date"><input type="date" style={{ width: 140 }} value={f.ordered_at}
          onChange={(e) => setF({ ...f, ordered_at: e.target.value })} /></Field>
      </div>
      <div className="row center">
        <button className="primary" disabled={!qty || qty > pile.on_hand} onClick={submit}>
          {qty > pile.on_hand ? `Only ${pile.on_hand} on hand` : 'Record sale'}</button>
        <Msg msg={msg} />
      </div>
    </Modal>
  )
}

import React, { useEffect, useRef, useState } from 'react'
import { api, fmtMoney } from '../api.js'
import { Field, Modal, Msg, useMsg } from '../components.jsx'

const STATUS_BADGE = {
  ready: 'green', blocked: 'yellow', committed: 'blue', duplicate: '',
}
const MATCH_BADGE = {
  matched: 'green', ambiguous: 'yellow', out_of_stock: 'yellow', unmatched: 'red',
}
const MATCH_LABEL = { out_of_stock: 'out of stock' }

const fmtDate = (iso) => (iso ? iso.slice(0, 10) : '—')

// A line's card identity, as read off the slip.
function lineLabel(l) {
  if (!l.parse_ok) return l.raw || l.description || '(unreadable)'
  const finish = l.printing_canonical && l.printing_canonical !== 'normal'
    ? ` ${l.printing_canonical}` : ''
  return `${l.card_name} · #${l.collector_number} · ${l.condition || l.condition_label}${finish}`
}

export default function OrderIntakePage() {
  const [msg, ok, err] = useMsg()
  const [batches, setBatches] = useState([])
  const [batch, setBatch] = useState(null)
  const [busy, setBusy] = useState(false)
  const [resolving, setResolving] = useState(null) // { slip, index }
  const fileRef = useRef(null)

  const loadBatches = () => api.get('/api/order-intake/batches').then(setBatches)
  useEffect(() => { loadBatches().catch(err) }, [])

  const openBatch = async (id) => {
    try { setBatch(await api.get(`/api/order-intake/batches/${id}`)) }
    catch (e) { err(e) }
  }

  const upload = async (file) => {
    if (!file) return
    setBusy(true)
    try {
      const b = await api.upload('/api/order-intake/upload', file)
      setBatch(b)
      await loadBatches()
      const ready = b.counts.ready || 0
      ok(`Read ${b.order_count} order(s) from ${b.filename} — ${ready} ready, ` +
        `${(b.counts.blocked || 0)} need attention`)
    } catch (e) { err(e) } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  // Every mutation returns the updated slip order, so the batch is patched in
  // place rather than refetched — keeps scroll position while resolving lines.
  const putSlip = (slip) => setBatch((b) => b && ({
    ...b, orders: b.orders.map((o) => (o.id === slip.id ? slip : o)),
  }))

  const patchSlip = async (slip, payload) => {
    try { putSlip(await api.patch(`/api/order-intake/orders/${slip.id}`, payload)) }
    catch (e) { err(e) }
  }

  const resolveLine = async (slip, index, payload) => {
    try {
      putSlip(await api.post(
        `/api/order-intake/orders/${slip.id}/lines/${index}/resolve`, payload))
      setResolving(null)
    } catch (e) { err(e) }
  }

  const rematch = async (slip) => {
    try {
      putSlip(await api.post(`/api/order-intake/orders/${slip.id}/rematch`))
      ok(`Re-matched ${slip.order_number}`)
    } catch (e) { err(e) }
  }

  const commitOne = async (slip) => {
    try {
      const r = await api.post(`/api/order-intake/orders/${slip.id}/commit`)
      putSlip(r.slip)
      ok(`${slip.order_number} → open order #${r.order_id}`)
    } catch (e) { err(e) }
  }

  const commitBatch = async () => {
    setBusy(true)
    try {
      const r = await api.post(`/api/order-intake/batches/${batch.id}/commit`)
      setBatch(r.batch)
      await loadBatches()
      ok(`Committed ${r.committed.length} order(s)` +
        (r.skipped.length ? `; ${r.skipped.length} left for review` : ''))
    } catch (e) { err(e) } finally { setBusy(false) }
  }

  const discard = async (id) => {
    if (!window.confirm(
      'Discard this review batch? Orders already committed from it stay live.')) return
    try {
      await api.del(`/api/order-intake/batches/${id}`)
      if (batch && batch.id === id) setBatch(null)
      await loadBatches()
      ok('Batch discarded')
    } catch (e) { err(e) }
  }

  const readyCount = batch ? (batch.counts.ready || 0) : 0

  return (
    <div>
      <h2>Order Intake</h2>
      <p className="muted" style={{ marginTop: -6 }}>
        Upload the TCGplayer packing-slip PDF. Each sheet becomes an order you can
        review, then commit as an open order ready for a pick list. Nothing touches
        inventory until you mark the order shipped.
      </p>

      <div className="panel">
        <div className="row center">
          <Field label="Packing-slip PDF">
            <input ref={fileRef} type="file" accept="application/pdf"
              disabled={busy}
              onChange={(e) => upload(e.target.files && e.target.files[0])} />
          </Field>
          {busy && <span className="muted">working…</span>}
        </div>
        <Msg msg={msg} />
        <div className="muted" style={{ fontSize: 12 }}>
          Card names, quantities, prices, the order date and the buyer's city/state
          are read straight from the PDF text — no OCR, so the numbers are exact.
        </div>
      </div>

      {batches.length > 0 && (
        <div className="panel">
          <h3>Recent uploads</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>File</th><th>Uploaded</th><th>Orders</th>
                  <th>Ready</th><th>Needs attention</th><th>Committed</th>
                  <th>Status</th><th></th>
                </tr>
              </thead>
              <tbody>
                {batches.map((b) => (
                  <tr key={b.id}
                    style={batch && batch.id === b.id
                      ? { outline: '1px solid var(--accent)' } : {}}>
                    <td>{b.filename}</td>
                    <td>{fmtDate(b.created_at)}</td>
                    <td>{b.order_count}</td>
                    <td>{b.counts.ready || 0}</td>
                    <td>{b.counts.blocked || 0}</td>
                    <td>{(b.counts.committed || 0)}
                      {b.counts.duplicate
                        ? <span className="muted"> (+{b.counts.duplicate} dup)</span>
                        : null}</td>
                    <td><span className="badge">{b.status}</span></td>
                    <td>
                      <button className="small" onClick={() => openBatch(b.id)}>Review</button>{' '}
                      <button className="small danger" onClick={() => discard(b.id)}>Discard</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {batch && (
        <>
          <div className="row center" style={{ justifyContent: 'space-between' }}>
            <h3 style={{ margin: 0 }}>{batch.filename}</h3>
            <div>
              <button className="primary" disabled={busy || !readyCount}
                onClick={commitBatch}>
                Commit {readyCount} ready order{readyCount === 1 ? '' : 's'}
              </button>
            </div>
          </div>
          {batch.orders.map((slip) => (
            <SlipCard key={slip.id} slip={slip}
              onPatch={(payload) => patchSlip(slip, payload)}
              onCommit={() => commitOne(slip)}
              onRematch={() => rematch(slip)}
              onSkip={(i) => resolveLine(slip, i, { skip: true })}
              onResolve={(i) => setResolving({ slip, index: i })} />
          ))}
        </>
      )}

      {resolving && (
        <ResolveModal slip={resolving.slip} index={resolving.index}
          onClose={() => setResolving(null)}
          onPick={(payload) => resolveLine(resolving.slip, resolving.index, payload)} />
      )}
    </div>
  )
}

function SlipCard({ slip, onPatch, onCommit, onRematch, onSkip, onResolve }) {
  const fee = slip.fee_detail || {}
  const locked = slip.status === 'committed' || slip.status === 'duplicate'
  const lineSum = (slip.lines || [])
    .reduce((t, l) => t + (l.line_total || 0), 0)
  return (
    <div className="panel">
      <div className="row center" style={{ justifyContent: 'space-between' }}>
        <div>
          <strong>{slip.order_number}</strong>{' '}
          <span className={`badge ${STATUS_BADGE[slip.status] || ''}`}>{slip.status}</span>
          {slip.page_count > 1 &&
            <span className="badge" title="this slip spans multiple pages">
              {slip.page_count} pages</span>}
          <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
            {slip.buyer_name} · {slip.ship_city}, {slip.ship_state}{' '}
            {slip.ship_postal_code} · ordered {fmtDate(slip.ordered_at)}
          </div>
        </div>
        <div>
          {!locked && <button className="small" onClick={onRematch}>Re-match</button>}{' '}
          {slip.status === 'ready' &&
            <button className="small primary" onClick={onCommit}>Commit</button>}
        </div>
      </div>

      {slip.error && <div className="error-text">{slip.error}</div>}
      {slip.warning && (
        <div style={{ color: 'var(--yellow)', fontSize: 13 }}>⚠ {slip.warning}</div>
      )}
      {!slip.reconciled && (
        <div className="error-text">
          Parsed lines total {fmtMoney(lineSum)} but the slip prints{' '}
          {fmtMoney(slip.item_total)} — check this one by hand.
        </div>
      )}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Qty</th><th>Card</th><th>Set</th><th>Match</th>
              <th>Unit</th><th>Total</th><th></th>
            </tr>
          </thead>
          <tbody>
            {(slip.lines || []).map((l, i) => (
              <tr key={i}>
                <td>{l.quantity}</td>
                <td>{lineLabel(l)}</td>
                <td className="muted">{l.set_name}</td>
                <td>
                  <span className={`badge ${MATCH_BADGE[l.match_status] || ''}`}>
                    {l.skipped ? 'skipped'
                      : (MATCH_LABEL[l.match_status] || l.match_status)}
                  </span>
                  {l.match_note &&
                    <div className="muted" style={{ fontSize: 11 }}>{l.match_note}</div>}
                </td>
                <td>{fmtMoney(l.unit_price)}</td>
                <td>{fmtMoney(l.line_total)}</td>
                <td>
                  {!locked && l.match_status !== 'matched' && (
                    <>
                      <button className="small" onClick={() => onResolve(i)}>Find…</button>{' '}
                      <button className="small" title="Commit without linking inventory — no COGS booked"
                        onClick={() => onSkip(i)}>Skip</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="row center" style={{ marginTop: 10 }}>
        <Field label="Buyer shipping paid">
          <input type="number" step="0.01" style={{ width: 90 }} disabled={locked}
            key={`ship-${slip.id}-${slip.shipping_charged}`}
            defaultValue={slip.shipping_charged || ''}
            title="Not printed on the slip — pre-filled from your flat rate. Counts as revenue and as part of the commission base."
            onBlur={(e) => onPatch({ shipping_charged: e.target.value || 0 })} />
        </Field>
        <Field label={`Fee${slip.fee_overridden ? '' : ' (estimated)'}`}>
          <input type="number" step="0.01" style={{ width: 90 }} disabled={locked}
            key={`fee-${slip.id}-${slip.estimated_fee}`}
            defaultValue={slip.estimated_fee ?? ''}
            title="Estimated from the order; type over it to set the real figure, or clear it to go back to the estimate."
            onBlur={(e) => onPatch({
              estimated_fee: e.target.value === '' ? null : e.target.value,
            })} />
        </Field>
        <div className="stat" style={{ minWidth: 110 }}>
          <div className="value">{fmtMoney(slip.item_total)}</div>
          <div className="label">items ({slip.quantity_total})</div>
        </div>
        <div className="stat" style={{ minWidth: 110 }}>
          <div className="value">
            {fmtMoney(round2((slip.item_total || 0) + (slip.shipping_charged || 0)
              - (slip.estimated_fee || 0)))}
          </div>
          <div className="label">net before COGS</div>
        </div>
        <div className="muted" style={{ fontSize: 11, maxWidth: 300 }}>
          {slip.fee_overridden ? (
            <>fee set by hand — <button className="small" disabled={locked}
              onClick={() => onPatch({ estimated_fee: null })}>use the estimate
              ({fmtMoney(fee.fee)})</button></>
          ) : (
            <>{fmtMoney(fee.commission)} commission + {fmtMoney(fee.processing)}{' '}
              processing
              {fee.tax_estimated && fee.tax_rate != null && (
                <> · tax estimated at {(fee.tax_rate * 100).toFixed(2)}% for{' '}
                  {slip.ship_state || 'unknown state'} ({fmtMoney(fee.tax)})</>
              )}</>
          )}
        </div>
      </div>
    </div>
  )
}

const round2 = (v) => Math.round(v * 100) / 100

// Resolution searches your inventory, never the catalog: the question is which
// of your cards this line is, and a catalog card you don't stock isn't an answer.
function ResolveModal({ slip, index, onClose, onPick }) {
  const [msg, ok, err] = useMsg()
  const line = (slip.lines || [])[index] || {}
  const candidates = line.candidates || []
  const [q, setQ] = useState(line.card_name || '')
  const [found, setFound] = useState(null)
  const [inStockOnly, setInStockOnly] = useState(true)

  const search = async () => {
    if (!q.trim()) return
    try {
      const r = await api.post('/api/inventory/search',
        { q: q.trim(), in_stock_only: inStockOnly, limit: 40 })
      setFound(r.items)
    } catch (e) { err(e) }
  }
  const label = (it) => it.card
    ? `${it.card.name} [${it.card.set_code} #${it.card.collector_number}] ${it.condition} ${it.printing}`
    : `${it.custom_name || 'item'} #${it.id}`

  const Row = ({ id, text, note }) => (
    <tr>
      <td>{text}</td>
      <td className="muted">{note}</td>
      <td><button className="small primary"
        onClick={() => onPick({ inventory_id: id })}>Use this</button></td>
    </tr>
  )

  return (
    <Modal wide title={`Match: ${lineLabel(line)}`} onClose={onClose}>
      <div className="muted" style={{ fontSize: 12 }}>
        Sold as {line.condition || line.condition_label}
        {line.printing_canonical && line.printing_canonical !== 'normal'
          ? ` ${line.printing_canonical}` : ''}, listed under {line.set_name || '—'}.
        {line.match_note ? ` ${line.match_note}.` : ''}
      </div>
      <Msg msg={msg} />

      {candidates.length > 0 && (
        <>
          <h4>In your inventory</h4>
          <div className="table-wrap">
            <table>
              <tbody>
                {candidates.map((c) => (
                  <Row key={c.inventory_id} id={c.inventory_id} text={c.label}
                    note={`${c.bin ? `bin ${c.bin} · ` : ''}qty ${c.quantity}`} />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h4>Search your inventory</h4>
      <div className="row center">
        <input style={{ width: 260 }} value={q} autoFocus
          placeholder="card name or comment"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search()} />
        <label className="muted" style={{ fontSize: 12 }}>
          <input type="checkbox" checked={inStockOnly}
            onChange={(e) => setInStockOnly(e.target.checked)} /> in stock only
        </label>
        <button onClick={search}>Search</button>
      </div>
      {found && (found.length ? (
        <div className="table-wrap" style={{ maxHeight: 300, overflowY: 'auto' }}>
          <table>
            <tbody>
              {found.map((it) => (
                <Row key={it.id} id={it.id} text={label(it)}
                  note={`${it.bin ? `bin ${it.bin} · ` : ''}qty ${it.quantity}`} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted" style={{ fontSize: 12 }}>
          Nothing in inventory matches that
          {inStockOnly ? ' and is in stock' : ''}. If the card really was sold,
          the stock record is what needs fixing — add it, then Re-match. Or Skip
          the line to record the sale without linking inventory.
        </p>
      ))}

      <div className="row" style={{ marginTop: 12 }}>
        <button onClick={() => onPick({ skip: true })}
          title="Keep the revenue, don't link inventory and don't book COGS">
          Skip this line
        </button>
        <button onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  )
}

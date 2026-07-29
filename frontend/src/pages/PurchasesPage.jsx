import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, fmtMoney } from '../api.js'
import { Msg, SortTh, useMsg, useSort } from '../components.jsx'

// Unit costs are allocated across a lot, so they carry more precision than money.
const fmtUnitCost = (v) => `$${Number(v).toFixed(4)}`
const fmtPct = (v) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`)

export default function PurchasesPage() {
  const [msg, , err] = useMsg()
  const [data, setData] = useState({ lots: [], totals: null, fee_rate: 0 })
  // Off by default: gross asking prices are what Inventory shows, and silently
  // discounting them here made the same stock look like two different numbers.
  const [netOfFees, setNetOfFees] = useState(false)
  const { sorted, sort, toggle } = useSort(data.lots)
  const navigate = useNavigate()

  useEffect(() => {
    api.get('/api/purchases/lots').then(setData).catch(err)
  }, [])

  const keep = netOfFees ? 1 - data.fee_rate : 1
  const askOf = (l) => l.ask * keep
  const projectedOf = (l) => l.revenue + askOf(l) - l.paid
  const roiOf = (l) => (l.paid ? (projectedOf(l) / l.paid) * 100 : null)

  const t = data.totals
  const totalAsk = t ? t.ask * keep : 0
  const totalProjected = t ? t.revenue + totalAsk - t.paid : 0

  // Drill into the purchase's FIFO pools rather than a cost match, so cards you
  // also owned from an earlier buy come along. Sold-out rows included.
  const viewCards = (lot) =>
    navigate(`/inventory?lot_date=${lot.date}&lot_cost=${lot.unit_cost}`)

  return (
    <div>
      <h2>Purchases <span className="muted">(reconstructed from acquisition batches)</span></h2>
      <p className="muted">
        What each buy cost and what it stands to make: <b>Projected</b> is
        realised revenue plus the unsold remainder at its asking price, less what
        you paid. Revenue is traced through the FIFO records to the units of that
        purchase which actually sold, net of fees, shipping and refunds. Unsold
        stock with no price set contributes nothing, so an unpriced bulk pile
        reads as a full loss until you price it.
      </p>
      <Msg msg={msg} />

      {t && (
        <div className="stats">
          <div className="stat"><div className="value">{t.purchases}</div>
            <div className="label">Purchases · {t.units} units</div></div>
          <div className="stat" title="Total spent on stock across every purchase">
            <div className="value">{fmtMoney(t.paid)}</div>
            <div className="label">Paid</div></div>
          <div className="stat" title="Realised, net of fees/shipping/refunds">
            <div className="value">{fmtMoney(t.revenue)}</div>
            <div className="label">Revenue so far</div></div>
          <div className="stat" title="What the unsold remainder is priced at">
            <div className="value">{fmtMoney(totalAsk)}</div>
            <div className="label">Unsold at ask{netOfFees ? ', net of fees' : ''} · {t.left} units</div></div>
          <div className="stat" title="revenue + unsold at ask − paid">
            <div className="value" style={{ color: totalProjected < 0 ? 'var(--red)' : undefined }}>
              {fmtMoney(totalProjected)}</div>
            <div className="label">Projected profit{t.unpriced_units
              ? ` · ${t.unpriced_units} units unpriced` : ''}</div></div>
        </div>
      )}

      <div className="row center">
        <label title="Off, asking prices are gross and match the Inventory listed total. On, they are discounted by the fee rate your own orders have realised — revenue is already net either way.">
          <input type="checkbox" checked={netOfFees}
            onChange={(e) => setNetOfFees(e.target.checked)} />
          {' '}discount asking by fees ({(data.fee_rate * 100).toFixed(1)}% — your realised rate)
        </label>
      </div>

      <div className="panel table-wrap"><table>
        <thead><tr>
          <SortTh k="date" accessor={(l) => l.date} sort={sort} toggle={toggle}>Date</SortTh>
          <SortTh k="cost" accessor={(l) => l.unit_cost} sort={sort} toggle={toggle}>Cost / unit</SortTh>
          <SortTh k="cards" accessor={(l) => l.cards} sort={sort} toggle={toggle}>Cards</SortTh>
          <SortTh k="units" accessor={(l) => l.units} sort={sort} toggle={toggle}>Units</SortTh>
          <SortTh k="paid" accessor={(l) => l.paid} sort={sort} toggle={toggle}>Paid</SortTh>
          <SortTh k="sold" accessor={(l) => l.sold} sort={sort} toggle={toggle}>Sold</SortTh>
          <SortTh k="revenue" accessor={(l) => l.revenue} sort={sort} toggle={toggle}>Revenue</SortTh>
          <SortTh k="left" accessor={(l) => l.left} sort={sort} toggle={toggle}>Left</SortTh>
          <SortTh k="ask" accessor={(l) => l.ask} sort={sort} toggle={toggle}
            title="Gross asking price of the unsold units, matching the Inventory listed total">
            Unsold at ask{netOfFees ? ' (net)' : ''}</SortTh>
          {/* Sort on the gross figures: useSort caches the accessor, so keying
              off the fee toggle would leave the order stale when it flips. */}
          <SortTh k="projected" accessor={(l) => l.projected} sort={sort} toggle={toggle}>Projected</SortTh>
          <SortTh k="roi" accessor={(l) => l.roi} sort={sort} toggle={toggle}>ROI</SortTh>
          <th></th>
        </tr></thead>
        <tbody>{sorted.map((l) => (
          <tr key={`${l.date}-${l.unit_cost}`}>
            <td>{l.date}</td>
            <td>{fmtUnitCost(l.unit_cost)}</td>
            <td>{l.cards}</td>
            <td>{l.units}</td>
            <td>{fmtMoney(l.paid)}</td>
            <td>{l.sold || <span className="muted">—</span>}
              {/* Units that left without being sold: supplier returns, undone
                  imports. Called out so units = left + sold + this always reads. */}
              {l.other_out > 0 && <> <span className="badge yellow"
                title="Units removed without a sale (supplier return, undone import)">
                +{l.other_out} other</span></>}</td>
            <td title={l.sold ? `cost of those units ${fmtMoney(l.cogs_sold)} · realised ${fmtMoney(l.profit_realized)}` : ''}>
              {l.sold ? fmtMoney(l.revenue) : <span className="muted">—</span>}</td>
            <td>{l.left}</td>
            <td>{fmtMoney(askOf(l))}
              {l.unpriced_units > 0 && <> <span className="badge yellow"
                title="Unsold units with no price set — they contribute nothing to the projection">
                {l.unpriced_units} unpriced</span></>}</td>
            <td style={{ color: projectedOf(l) < 0 ? 'var(--red)' : undefined }}>
              {fmtMoney(projectedOf(l))}</td>
            <td>{fmtPct(roiOf(l))}</td>
            <td><button className="small" onClick={() => viewCards(l)}>View cards</button></td>
          </tr>))}
        </tbody>
      </table></div>
    </div>
  )
}

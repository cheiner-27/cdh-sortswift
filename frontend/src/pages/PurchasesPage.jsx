import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, fmtMoney } from '../api.js'
import { Msg, SortTh, useMsg, useSort } from '../components.jsx'

// Unit costs are allocated across a lot, so they carry more precision than money.
const fmtUnitCost = (v) => `$${Number(v).toFixed(4)}`

export default function PurchasesPage() {
  const [msg, , err] = useMsg()
  const [data, setData] = useState({ lots: [], totals: null })
  const { sorted, sort, toggle } = useSort(data.lots)
  const navigate = useNavigate()

  useEffect(() => {
    api.get('/api/purchases/lots').then(setData).catch(err)
  }, [])

  // Drill into the purchase's FIFO pools rather than a cost match, so cards you
  // also owned from an earlier buy come along. Sold-out rows included.
  const viewCards = (lot) =>
    navigate(`/inventory?lot_date=${lot.date}&lot_cost=${lot.unit_cost}`)

  return (
    <div>
      <h2>Purchases <span className="muted">(reconstructed from acquisition batches)</span></h2>
      <p className="muted">
        What each buy cost and how much of it is still on the shelf. Unlike the
        Inventory totals — which value on-hand stock, so a sold card counts for
        nothing — <b>Units</b> and <b>Paid</b> are what you bought and stay put as
        stock sells through. Check <b>Paid</b> against the invoice to confirm the
        whole purchase made it in.
      </p>
      <Msg msg={msg} />

      {data.totals && (
        <div className="stats">
          <div className="stat"><div className="value">{data.totals.purchases}</div>
            <div className="label">Purchases</div></div>
          <div className="stat"><div className="value">{data.totals.units}</div>
            <div className="label">Units bought</div></div>
          <div className="stat" title="Total spent on stock across every purchase">
            <div className="value">{fmtMoney(data.totals.paid)}</div>
            <div className="label">Paid</div></div>
          <div className="stat"><div className="value">{data.totals.left}</div>
            <div className="label">Units unsold</div></div>
          <div className="stat" title="What the on-hand stock from these purchases is listed at">
            <div className="value">{fmtMoney(data.totals.ask)}</div>
            <div className="label">Asking</div></div>
        </div>
      )}

      <div className="panel table-wrap"><table>
        <thead><tr>
          <SortTh k="date" accessor={(l) => l.date} sort={sort} toggle={toggle}>Date</SortTh>
          <SortTh k="cost" accessor={(l) => l.unit_cost} sort={sort} toggle={toggle}>Cost / unit</SortTh>
          <SortTh k="cards" accessor={(l) => l.cards} sort={sort} toggle={toggle}>Cards</SortTh>
          <SortTh k="units" accessor={(l) => l.units} sort={sort} toggle={toggle}>Units</SortTh>
          <SortTh k="paid" accessor={(l) => l.paid} sort={sort} toggle={toggle}>Paid</SortTh>
          <SortTh k="sold" accessor={(l) => l.sold} sort={sort} toggle={toggle}>Sold</SortTh>
          <SortTh k="left" accessor={(l) => l.left} sort={sort} toggle={toggle}>Left</SortTh>
          <SortTh k="ask" accessor={(l) => l.ask} sort={sort} toggle={toggle}>Asking</SortTh>
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
            <td>{l.left}</td>
            <td>{fmtMoney(l.ask)}</td>
            <td><button className="small" onClick={() => viewCards(l)}>View cards</button></td>
          </tr>))}
        </tbody>
      </table></div>
    </div>
  )
}

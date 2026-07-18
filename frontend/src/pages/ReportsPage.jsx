import React, { useEffect, useState } from 'react'
import { api, fmtMoney } from '../api.js'
import { Field, SortTh, useSort } from '../components.jsx'

export default function ReportsPage() {
  const [groupBy, setGroupBy] = useState('month')
  const [pnl, setPnl] = useState([])
  const [aging, setAging] = useState(null)
  const [locations, setLocations] = useState([])
  const [expenses, setExpenses] = useState(null)
  const pnlSort = useSort(pnl)
  const locSort = useSort(locations)

  useEffect(() => { api.get(`/api/reports/pnl?group_by=${groupBy}`).then(setPnl) }, [groupBy])
  useEffect(() => {
    api.get('/api/reports/aging').then(setAging)
    api.get('/api/reports/locations').then(setLocations)
    api.get('/api/expenses/summary').then(setExpenses)
  }, [])

  const salesProfit = pnl.reduce((s, r) => s + (r.profit || 0), 0)
  const opex = expenses?.total_opex || 0
  const capex = expenses?.total_capex || 0
  const expenseTotal = expenses?.total || 0
  const net = salesProfit - expenseTotal

  return (
    <div>
      <h2>Reports</h2>

      <div className="stats">
        <div className="stat"><div className="value" style={{ color: salesProfit >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmtMoney(salesProfit)}</div>
          <div className="label">sales profit (all groups)</div></div>
        <div className="stat"><div className="value" style={{ color: 'var(--red)' }}>−{fmtMoney(opex)}</div>
          <div className="label">operating expenses (opex)</div></div>
        <div className="stat"><div className="value" style={{ color: 'var(--red)' }}>−{fmtMoney(capex)}</div>
          <div className="label">capital expenditures (capex)</div></div>
        <div className="stat"><div className="value" style={{ color: net >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmtMoney(net)}</div>
          <div className="label">net profit</div></div>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: -8 }}>Net = sales profit − operating expenses − capital expenditures. <b>Opex</b> is recurring overhead (supplies, postage, software); <b>capex</b> is durable equipment. Both are expensed in-period here (de minimis safe harbor — all items are under the $2,500/item threshold), but shown separately. Expenses are tracked on the <b>Expenses</b> page; the sales-profit total sums the P&L groups below, and expenses here are all-time.</p>

      <div className="panel">
        <div className="row center">
          <h3 style={{ margin: 0 }}>Realized P&L</h3>
          <Field label="Group by"><select value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
            <option>day</option><option>week</option><option>month</option>
            <option>game</option><option>set</option></select></Field>
        </div>
        <table><thead><tr>
          <SortTh k="group" accessor={(r) => r.group} sort={pnlSort.sort} toggle={pnlSort.toggle}>Group</SortTh>
          <SortTh k="orders" accessor={(r) => r.orders} sort={pnlSort.sort} toggle={pnlSort.toggle}>Orders</SortTh>
          <SortTh k="units" accessor={(r) => r.units} sort={pnlSort.sort} toggle={pnlSort.toggle}>Units</SortTh>
          <SortTh k="revenue" accessor={(r) => r.revenue} sort={pnlSort.sort} toggle={pnlSort.toggle}>Revenue</SortTh>
          <SortTh k="refunds" accessor={(r) => r.refunds} sort={pnlSort.sort} toggle={pnlSort.toggle}>Refunds</SortTh>
          <SortTh k="cogs" accessor={(r) => r.cogs} sort={pnlSort.sort} toggle={pnlSort.toggle}>COGS</SortTh>
          <SortTh k="shipping" accessor={(r) => r.shipping} sort={pnlSort.sort} toggle={pnlSort.toggle}>Shipping</SortTh>
          <SortTh k="fees" accessor={(r) => r.fees} sort={pnlSort.sort} toggle={pnlSort.toggle}>Fees</SortTh>
          <SortTh k="profit" accessor={(r) => r.profit} sort={pnlSort.sort} toggle={pnlSort.toggle}>Profit</SortTh>
        </tr></thead>
          <tbody>{pnlSort.sorted.map((r) => (
            <tr key={r.group}>
              <td>{r.group}</td><td>{r.orders}</td><td>{r.units}</td>
              <td>{fmtMoney(r.revenue)}</td>
              <td>{r.refunds ? <span style={{ color: 'var(--red)' }}>−{fmtMoney(r.refunds)}</span> : '—'}</td>
              <td>{fmtMoney(r.cogs)}</td>
              <td>{fmtMoney(r.shipping)}</td><td>{fmtMoney(r.fees)}</td>
              <td style={{ color: r.profit >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmtMoney(r.profit)}</td>
            </tr>))}</tbody></table>
        {pnl.length === 0 && <p className="muted">No fulfilled orders yet. P&L = revenue − refunds − FIFO COGS − shipping (incl. returns) − marketplace fees. Shipping includes return postage; refunds cover partial and full customer refunds.</p>}
      </div>

      {aging && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Inventory aging</h3>
          <table><thead><tr><th>Age bucket</th><th>Units</th><th>Value at cost</th><th>Value at market</th></tr></thead>
            <tbody>
              {Object.entries(aging.buckets).map(([k, b]) => (
                <tr key={k}><td>{k}</td><td>{b.units}</td>
                  <td>{fmtMoney(b.cost_value)}</td><td>{fmtMoney(b.market_value)}</td></tr>))}
              <tr><td className="muted">unknown age</td><td>{aging.unknown_age.units}</td>
                <td>{fmtMoney(aging.unknown_age.cost_value)}</td>
                <td>{fmtMoney(aging.unknown_age.market_value)}</td></tr>
            </tbody></table>
          <p><b>Total:</b> {fmtMoney(aging.total_at_cost)} at cost · {fmtMoney(aging.total_at_market)} at market</p>
        </div>
      )}

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Location summary</h3>
        <p className="muted">Spot orphaned/mistyped bins here, then fix via Inventory → bulk transfer.</p>
        <table><thead><tr>
          <SortTh k="bin" accessor={(l) => l.bin} sort={locSort.sort} toggle={locSort.toggle}>Bin</SortTh>
          <SortTh k="records" accessor={(l) => l.records} sort={locSort.sort} toggle={locSort.toggle}>Records</SortTh>
          <SortTh k="units" accessor={(l) => l.units} sort={locSort.sort} toggle={locSort.toggle}>Units</SortTh>
          <SortTh k="value" accessor={(l) => l.value} sort={locSort.sort} toggle={locSort.toggle}>Value</SortTh>
        </tr></thead>
          <tbody>{locSort.sorted.map((l) => (
            <tr key={l.bin}><td>{l.bin}</td><td>{l.records}</td>
              <td>{l.units}</td><td>{fmtMoney(l.value)}</td></tr>))}</tbody></table>
      </div>
    </div>
  )
}

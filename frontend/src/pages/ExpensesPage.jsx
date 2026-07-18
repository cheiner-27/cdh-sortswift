import React, { useEffect, useState } from 'react'
import { api, fmtMoney } from '../api.js'
import { DropdownWithAdd, Field, Modal, Msg, SortTh, useMsg, useSort } from '../components.jsx'

const today = () => new Date().toISOString().slice(0, 10)

export default function ExpensesPage() {
  const [msg, ok, err] = useMsg()
  const [rows, setRows] = useState([])
  const { sorted, sort, toggle } = useSort(rows)
  const [summary, setSummary] = useState(null)
  const [range, setRange] = useState({ date_from: '', date_to: '' })
  const [sugg, setSugg] = useState({ retailers: [], categories: [], payment_methods: [] })
  const [edit, setEdit] = useState(null)

  const qs = () => {
    const p = new URLSearchParams()
    if (range.date_from) p.set('date_from', range.date_from)
    if (range.date_to) p.set('date_to', range.date_to)
    return p.toString() ? `?${p}` : ''
  }
  const refresh = () => {
    api.get(`/api/expenses${qs()}`).then(setRows)
    api.get(`/api/expenses/summary${qs()}`).then(setSummary)
    api.get('/api/expenses/suggestions').then(setSugg)
  }
  useEffect(refresh, [range.date_from, range.date_to])

  const del = async (id) => {
    if (!window.confirm('Delete this expense?')) return
    await api.del(`/api/expenses/${id}`); refresh()
  }

  return (
    <div>
      <h2>Expenses <span className="muted">(supplies, postage, software, equipment)</span></h2>
      <div className="row center">
        <Field label="From"><input type="date" value={range.date_from}
          onChange={(e) => setRange({ ...range, date_from: e.target.value })} /></Field>
        <Field label="To"><input type="date" value={range.date_to}
          onChange={(e) => setRange({ ...range, date_to: e.target.value })} /></Field>
        {(range.date_from || range.date_to) &&
          <button className="small" onClick={() => setRange({ date_from: '', date_to: '' })}>clear</button>}
        <span style={{ flex: 1 }} />
        <button className="primary" onClick={() => setEdit({
          date: today(), name: '', category: '', retailer: '', payment_method: '',
          quantity: 1, subtotal: '', tax_override: '', notes: '',
        })}>+ New expense</button>
      </div>
      <Msg msg={msg} />

      {summary && (
        <div className="stats">
          <div className="stat"><div className="value">{fmtMoney(summary.total)}</div>
            <div className="label">{summary.count} expense(s){range.date_from || range.date_to ? ' in range' : ' all time'}</div></div>
          <div className="stat"><div className="value">{fmtMoney(summary.total_subtotal)}</div><div className="label">subtotal</div></div>
          <div className="stat"><div className="value">{fmtMoney(summary.total_tax)}</div><div className="label">tax</div></div>
          {summary.by_category.slice(0, 4).map((c) => (
            <div className="stat" key={c.key}><div className="value">{fmtMoney(c.total)}</div>
              <div className="label">{c.key}</div></div>))}
        </div>
      )}

      <div className="panel table-wrap"><table>
        <thead><tr>
          <SortTh k="date" accessor={(e) => e.date} sort={sort} toggle={toggle}>Date</SortTh>
          <SortTh k="name" accessor={(e) => e.name} sort={sort} toggle={toggle}>Name</SortTh>
          <SortTh k="category" accessor={(e) => e.category} sort={sort} toggle={toggle}>Category</SortTh>
          <SortTh k="retailer" accessor={(e) => e.retailer} sort={sort} toggle={toggle}>Retailer</SortTh>
          <SortTh k="qty" accessor={(e) => e.quantity} sort={sort} toggle={toggle}>Qty</SortTh>
          <SortTh k="subtotal" accessor={(e) => e.subtotal} sort={sort} toggle={toggle}>Subtotal</SortTh>
          <SortTh k="tax" accessor={(e) => e.tax} sort={sort} toggle={toggle}>Tax</SortTh>
          <SortTh k="total" accessor={(e) => e.total} sort={sort} toggle={toggle}>Total</SortTh>
          <SortTh k="paid" accessor={(e) => e.payment_method} sort={sort} toggle={toggle}>Paid with</SortTh>
          <th></th></tr></thead>
        <tbody>{sorted.map((e) => (
          <tr key={e.id}>
            <td className="muted">{e.date || '—'}</td>
            <td>{e.name}{e.notes && <div className="muted" style={{ fontSize: 11 }}>{e.notes}</div>}</td>
            <td className="muted">{e.category || '—'}</td>
            <td>{e.retailer || '—'}</td>
            <td>{e.quantity}</td>
            <td>{fmtMoney(e.subtotal)}</td>
            <td className="muted">{fmtMoney(e.tax)}{e.tax_override != null && <span className="badge yellow" style={{ marginLeft: 4 }}>ovr</span>}</td>
            <td><b>{fmtMoney(e.total)}</b></td>
            <td className="muted">{e.payment_method || '—'}</td>
            <td>
              <button className="small" onClick={() => setEdit(e)}>Edit</button>{' '}
              <button className="small danger" onClick={() => del(e.id)}>Del</button>
            </td>
          </tr>))}</tbody>
      </table>
        {rows.length === 0 && <p className="muted">No expenses recorded. These are overhead not tied to a card — they feed Reports → net profit.</p>}
      </div>

      {edit && <ExpenseModal expense={edit} sugg={sugg}
        onClose={() => { setEdit(null); refresh() }} />}
    </div>
  )
}

function ExpenseModal({ expense, sugg, onClose }) {
  const [msg, ok, err] = useMsg()
  const [e, setE] = useState(structuredClone(expense))
  const set = (k, v) => setE({ ...e, [k]: v })
  const save = async () => {
    try {
      const body = {
        ...e,
        quantity: Number(e.quantity) || 1,
        subtotal: e.subtotal === '' ? 0 : Number(e.subtotal),
        tax_override: e.tax_override === '' || e.tax_override == null ? null : Number(e.tax_override),
      }
      if (e.id) await api.put(`/api/expenses/${e.id}`, body)
      else await api.post('/api/expenses', body)
      ok('Saved'); onClose()
    } catch (err2) { err(err2) }
  }
  return (
    <Modal title={e.id ? `Edit expense — ${e.name}` : 'New expense'} onClose={onClose} wide>
      <div className="row">
        <Field label="Date"><input type="date" value={e.date || ''} onChange={(ev) => set('date', ev.target.value)} /></Field>
        <Field label="Name"><input style={{ width: 220 }} value={e.name} placeholder="e.g. Penny Sleeves"
          onChange={(ev) => set('name', ev.target.value)} /></Field>
        <Field label="Category">
          <DropdownWithAdd value={e.category} onChange={(v) => set('category', v)}
            options={sugg.categories} width={150} placeholder="select…" /></Field>
        <Field label="Retailer">
          <DropdownWithAdd value={e.retailer} onChange={(v) => set('retailer', v)}
            options={sugg.retailers} width={150} placeholder="select…" /></Field>
      </div>
      <div className="row">
        <Field label="Qty"><input type="number" min="1" style={{ width: 70 }} value={e.quantity}
          onChange={(ev) => set('quantity', ev.target.value)} /></Field>
        <Field label="Subtotal ($, pre-tax)"><input style={{ width: 90 }} value={e.subtotal}
          onChange={(ev) => set('subtotal', ev.target.value)} /></Field>
        <Field label="Tax override ($, blank = auto)"><input style={{ width: 110 }} value={e.tax_override ?? ''}
          onChange={(ev) => set('tax_override', ev.target.value)} /></Field>
        <Field label="Paid with"><input list="exp-pay" style={{ width: 150 }} value={e.payment_method}
          placeholder="e.g. Capital One Venture" onChange={(ev) => set('payment_method', ev.target.value)} />
          <datalist id="exp-pay">{sugg.payment_methods.map((p) => <option key={p} value={p} />)}</datalist></Field>
      </div>
      <div className="row">
        <Field label="Notes"><textarea rows={2} style={{ width: 460 }} value={e.notes}
          onChange={(ev) => set('notes', ev.target.value)} /></Field>
      </div>
      <p className="muted" style={{ fontSize: 12 }}>Tax auto-calculates at the default rate (Settings) of the subtotal; enter an override for exact amounts. Total = subtotal + tax.</p>
      <div className="row center">
        <button className="primary" onClick={save}>Save expense</button>
        <Msg msg={msg} />
      </div>
    </Modal>
  )
}

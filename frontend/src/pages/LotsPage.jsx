import React, { useEffect, useState } from 'react'
import { api, fmtMoney } from '../api.js'
import { Field, Modal, Msg, SortTh, useMeta, useMsg, useSort } from '../components.jsx'

export default function LotsPage() {
  const meta = useMeta()
  const [msg, ok, err] = useMsg()
  const [templates, setTemplates] = useState([])
  const [lots, setLots] = useState([])
  const tpl = useSort(templates)
  const lot = useSort(lots)
  const [editTpl, setEditTpl] = useState(null)
  const [viewLot, setViewLot] = useState(null)

  const refresh = () => {
    api.get('/api/lots/templates').then(setTemplates)
    api.get('/api/lots').then(setLots)
  }
  useEffect(refresh, [])

  const generate = async (t) => {
    try {
      const lot = await api.post(`/api/lots/generate/${t.id}`, {})
      ok(`Generated "${lot.name}": ${lot.items.length} line(s), value ${fmtMoney(lot.total_value)}, price ${fmtMoney(lot.price)}`)
      refresh()
    } catch (e) { err(e) }
  }

  if (!meta) return null
  return (
    <div>
      <h2>Bulk Lots</h2>
      <Msg msg={msg} />
      <div className="panel">
        <div className="row center">
          <h3 style={{ margin: 0 }}>Templates</h3>
          <span style={{ flex: 1 }} />
          <button onClick={() => setEditTpl({ name: '', filters: {}, lot_size: 100, pricing_method: 'value_margin', margin_pct: 80, max_duplicates: 4 })}>+ New template</button>
        </div>
        <table><thead><tr>
          <SortTh k="name" accessor={(t) => t.name} sort={tpl.sort} toggle={tpl.toggle}>Name</SortTh>
          <th>Filters</th>
          <SortTh k="size" accessor={(t) => t.lot_size} sort={tpl.sort} toggle={tpl.toggle}>Size</SortTh>
          <th>Pricing</th>
          <SortTh k="dupes" accessor={(t) => t.max_duplicates} sort={tpl.sort} toggle={tpl.toggle}>Max dupes</SortTh>
          <th></th></tr></thead>
          <tbody>{tpl.sorted.map((t) => (
            <tr key={t.id}>
              <td>{t.name}<div className="muted">{t.description}</div></td>
              <td className="muted" style={{ fontSize: 12 }}>{summarizeFilters(t.filters)}</td>
              <td>{t.lot_size}</td>
              <td>{t.pricing_method === 'fixed' ? `fixed ${fmtMoney(t.fixed_price)}` : `${t.margin_pct}% of value`}</td>
              <td>{t.max_duplicates}</td>
              <td>
                <button className="small primary" onClick={() => generate(t)}>Generate lot</button>{' '}
                <button className="small" onClick={() => setEditTpl(t)}>Edit</button>{' '}
                <button className="small danger" onClick={async () => { await api.del(`/api/lots/templates/${t.id}`); refresh() }}>Del</button>
              </td>
            </tr>))}</tbody></table>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Lots</h3>
        <p className="muted">Cards in open/listed lots are reserved — excluded from marketplace push quantities until the lot sells or is dissolved.</p>
        <table><thead><tr>
          <SortTh k="name" accessor={(l) => l.name} sort={lot.sort} toggle={lot.toggle}>Name</SortTh>
          <SortTh k="status" accessor={(l) => l.status} sort={lot.sort} toggle={lot.toggle}>Status</SortTh>
          <SortTh k="cards" accessor={(l) => l.items.reduce((s, i) => s + i.quantity, 0)} sort={lot.sort} toggle={lot.toggle}>Cards</SortTh>
          <SortTh k="value" accessor={(l) => l.total_value} sort={lot.sort} toggle={lot.toggle}>Value</SortTh>
          <SortTh k="price" accessor={(l) => l.price} sort={lot.sort} toggle={lot.toggle}>Price</SortTh>
          <SortTh k="created" accessor={(l) => l.created_at} sort={lot.sort} toggle={lot.toggle}>Created</SortTh>
          <th></th></tr></thead>
          <tbody>{lot.sorted.map((l) => (
            <tr key={l.id}>
              <td>{l.name}</td>
              <td><span className={`badge ${l.status === 'sold' ? 'green' : l.status === 'dissolved' ? '' : 'blue'}`}>{l.status}</span></td>
              <td>{l.items.reduce((s, i) => s + i.quantity, 0)}</td>
              <td>{fmtMoney(l.total_value)}</td>
              <td>{fmtMoney(l.price)}</td>
              <td className="muted">{l.created_at?.slice(0, 10)}</td>
              <td>
                <button className="small" onClick={() => setViewLot(l)}>Contents</button>{' '}
                {(l.status === 'open' || l.status === 'listed') && (<>
                  <button className="small" onClick={async () => { await api.post(`/api/lots/${l.id}/mark-listed`, { marketplace: 'ebay' }); refresh() }}>Mark listed</button>{' '}
                  <button className="small primary" onClick={async () => {
                    try { const r = await api.post(`/api/lots/${l.id}/sell`); ok(`Lot sold, COGS ${fmtMoney(r.cogs)}`); refresh() } catch (e) { err(e) }
                  }}>Sold</button>{' '}
                  <button className="small danger" onClick={async () => { await api.post(`/api/lots/${l.id}/dissolve`); refresh() }}>Dissolve</button>
                </>)}
              </td>
            </tr>))}</tbody></table>
      </div>

      {editTpl && <TemplateModal meta={meta} tpl={editTpl} onClose={() => { setEditTpl(null); refresh() }} />}
      {viewLot && (
        <Modal title={`Lot — ${viewLot.name}`} onClose={() => setViewLot(null)} wide>
          <table><thead><tr><th>Card</th><th>Set</th><th>Qty</th><th>Unit value</th></tr></thead>
            <tbody>{viewLot.items.map((i) => (
              <tr key={i.id}><td>{i.name}</td><td>{i.set_code}</td><td>{i.quantity}</td><td>{fmtMoney(i.unit_value)}</td></tr>))}</tbody></table>
        </Modal>
      )}
    </div>
  )
}

function summarizeFilters(f) {
  if (!f) return 'any card'
  const parts = []
  if (f.games?.length) parts.push(f.games.join('/'))
  if (f.sets?.length) parts.push(`sets ${f.sets.join('/')}`)
  if (f.rarities?.length) parts.push(f.rarities.join('/'))
  if (f.conditions?.length) parts.push(f.conditions.join('/'))
  if (f.price_min != null || f.price_max != null) parts.push(`$${f.price_min ?? 0}–${f.price_max ?? '∞'}`)
  return parts.length ? parts.join(', ') : 'any card'
}

function MultiCheck({ label, options, selected, onChange }) {
  const toggle = (o) => onChange(selected.includes(o) ? selected.filter((x) => x !== o) : [...selected, o])
  return (
    <div>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{label}</div>
      <div style={{ maxHeight: 150, overflowY: 'auto' }}>
        {options.map((o) => (
          <label key={o} style={{ display: 'block', fontSize: 13 }}>
            <input type="checkbox" checked={selected.includes(o)} onChange={() => toggle(o)} /> {o}
          </label>))}
      </div>
    </div>
  )
}

function TemplateModal({ meta, tpl, onClose }) {
  const [msg, ok, err] = useMsg()
  const [t, setT] = useState(structuredClone(tpl))
  const f = t.filters || {}
  const setF = (k, v) => setT({ ...t, filters: { ...f, [k]: v } })
  const save = async () => {
    try {
      if (t.id) await api.put(`/api/lots/templates/${t.id}`, t)
      else await api.post('/api/lots/templates', t)
      ok('Saved'); onClose()
    } catch (e) { err(e) }
  }
  return (
    <Modal title={t.id ? `Edit template — ${t.name}` : 'New lot template'} onClose={onClose} wide>
      <div className="row">
        <Field label="Name"><input value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} /></Field>
        <Field label="Description"><input style={{ width: 260 }} value={t.description || ''}
          onChange={(e) => setT({ ...t, description: e.target.value })} /></Field>
        <Field label="Lot size (cards)"><input type="number" style={{ width: 70 }} value={t.lot_size}
          onChange={(e) => setT({ ...t, lot_size: Number(e.target.value) })} /></Field>
        <Field label="Max copies per card"><input type="number" style={{ width: 60 }} value={t.max_duplicates}
          onChange={(e) => setT({ ...t, max_duplicates: Number(e.target.value) })} /></Field>
      </div>
      <h3>Which cards can go in this lot?</h3>
      <p className="muted" style={{ marginTop: 0, fontSize: 12 }}>
        Leave a group unchecked to mean "any". The builder pulls in-stock, un-reserved cards matching
        <i>all</i> of the criteria below, newest/highest-value first, up to the lot size.
      </p>
      <div className="row" style={{ gap: 28, alignItems: 'flex-start' }}>
        <MultiCheck label="Games" options={meta.games}
          selected={f.games || []} onChange={(v) => setF('games', v.length ? v : undefined)} />
        <MultiCheck label="Rarities" options={meta.rarities}
          selected={f.rarities || []} onChange={(v) => setF('rarities', v.length ? v : undefined)} />
        <MultiCheck label="Conditions" options={meta.conditions}
          selected={f.conditions || []} onChange={(v) => setF('conditions', v.length ? v : undefined)} />
        <div>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>Set codes</div>
          <Field label="comma-separated (e.g. MH3, OP01) — blank = any">
            <input style={{ width: 180 }} value={(f.sets || []).join(', ')}
              onChange={(e) => setF('sets', e.target.value.trim() ? e.target.value.split(',').map((s) => s.trim().toUpperCase()).filter(Boolean) : undefined)} /></Field>
          <div style={{ fontSize: 12, fontWeight: 600, margin: '10px 0 4px' }}>Card value range</div>
          <div className="row center">
            <Field label="min $"><input style={{ width: 60 }} value={f.price_min ?? ''}
              onChange={(e) => setF('price_min', e.target.value === '' ? undefined : Number(e.target.value))} /></Field>
            <Field label="max $"><input style={{ width: 60 }} value={f.price_max ?? ''}
              onChange={(e) => setF('price_max', e.target.value === '' ? undefined : Number(e.target.value))} /></Field>
          </div>
        </div>
      </div>
      <h3>Pricing</h3>
      <div className="row center">
        <Field label="Method"><select value={t.pricing_method}
          onChange={(e) => setT({ ...t, pricing_method: e.target.value })}>
          <option value="value_margin">total value × margin %</option>
          <option value="fixed">fixed price</option></select></Field>
        {t.pricing_method === 'value_margin'
          ? <Field label="Margin %"><input style={{ width: 60 }} value={t.margin_pct}
            onChange={(e) => setT({ ...t, margin_pct: Number(e.target.value) })} /></Field>
          : <Field label="Fixed $"><input style={{ width: 80 }} value={t.fixed_price ?? ''}
            onChange={(e) => setT({ ...t, fixed_price: Number(e.target.value) })} /></Field>}
        <button className="primary" onClick={save}>Save template</button>
        <Msg msg={msg} />
      </div>
    </Modal>
  )
}

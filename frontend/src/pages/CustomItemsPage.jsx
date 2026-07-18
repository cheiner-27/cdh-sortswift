import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Field, Modal, Msg, SortTh, useMeta, useMsg, useSort } from '../components.jsx'

const ITEM_TYPES = ['graded_card', 'sealed', 'accessory', 'other']
const GRADERS = ['PSA', 'BGS', 'CGC', 'SGC', 'Raw']

export default function CustomItemsPage() {
  const [msg, ok, err] = useMsg()
  const [products, setProducts] = useState([])
  const { sorted, sort, toggle } = useSort(products)
  const [q, setQ] = useState('')
  const [edit, setEdit] = useState(null)
  const [upc, setUpc] = useState('')
  const [breakdownFor, setBreakdownFor] = useState(null)

  const refresh = () =>
    api.get(`/api/custom/products${q ? `?q=${encodeURIComponent(q)}` : ''}`).then(setProducts)
  useEffect(() => { refresh() }, [])

  const lookupUpc = async () => {
    try {
      const p = await api.get(`/api/custom/upc/${encodeURIComponent(upc)}`)
      ok(`UPC match: ${p.name}`)
      setEdit(p)
    } catch (e) { err(e) }
  }

  return (
    <div>
      <h2>Custom Items <span className="muted">(graded, sealed, accessories)</span></h2>
      <div className="row center">
        <input placeholder="search products" value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && refresh()} />
        <button onClick={refresh}>Search</button>
        <input placeholder="UPC lookup" value={upc} onChange={(e) => setUpc(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && lookupUpc()} style={{ width: 140 }} />
        <button onClick={lookupUpc}>Lookup</button>
        <span style={{ flex: 1 }} />
        <button className="primary" onClick={() => setEdit({
          category: 'Other', group: '', name: '', item_type: 'other',
          description: '', images: [], upc: '', breakdown_components: [], skus: [{}],
        })}>+ New product</button>
      </div>
      <Msg msg={msg} />

      <div className="panel table-wrap"><table>
        <thead><tr>
          <SortTh k="name" accessor={(p) => p.name} sort={sort} toggle={toggle}>Product</SortTh>
          <SortTh k="type" accessor={(p) => p.item_type} sort={sort} toggle={toggle}>Type</SortTh>
          <SortTh k="cat" accessor={(p) => p.category} sort={sort} toggle={toggle}>Category › Group</SortTh>
          <SortTh k="upc" accessor={(p) => p.upc} sort={sort} toggle={toggle}>UPC</SortTh>
          <th>SKUs</th><th></th></tr></thead>
        <tbody>{sorted.map((p) => (
          <tr key={p.id}>
            <td>
              {p.images[0] && <img className="card-img" src={p.images[0]} alt="" />}
              {p.name}
              <div className="muted">{p.description?.slice(0, 80)}</div>
            </td>
            <td><span className="badge">{p.item_type}</span></td>
            <td className="muted">{p.category}{p.group ? ` › ${p.group}` : ''}</td>
            <td className="muted">{p.upc || '—'}</td>
            <td>{p.skus.map((s) => (
              <div key={s.id} className="muted" style={{ fontSize: 12 }}>
                #{s.id} {s.grading_company ? `${s.grading_company} ${s.grade_value || ''} cert ${s.cert_number || '—'}` : [s.condition, s.printing, s.language].filter(Boolean).join(' / ') || 'default'}
              </div>))}</td>
            <td>
              <button className="small" onClick={() => setEdit(p)}>Edit</button>{' '}
              {p.item_type === 'sealed' && p.breakdown_components?.length > 0 &&
                <button className="small" onClick={() => setBreakdownFor(p)}>Break down…</button>}
            </td>
          </tr>))}</tbody>
      </table>
        <p className="muted">Defining a product doesn't stock it — add stock via Staging → Manual add (pick the custom SKU) or the UPC lookup path. Sealed products with components support "Break Down Sealed Product" from an inventory row.</p>
      </div>

      {edit && <ProductModal product={edit} onClose={() => { setEdit(null); refresh() }} />}
      {breakdownFor && <BreakdownModal product={breakdownFor} onClose={() => setBreakdownFor(null)} />}
    </div>
  )
}

function ProductModal({ product, onClose }) {
  const meta = useMeta()
  const [msg, ok, err] = useMsg()
  const [p, setP] = useState(structuredClone(product))
  const isGraded = p.item_type === 'graded_card'
  const categories = meta?.custom_categories || ['Other']
  const save = async () => {
    try {
      if (p.id) await api.put(`/api/custom/products/${p.id}`, p)
      else await api.post('/api/custom/products', p)
      ok('Saved'); onClose()
    } catch (e) { err(e) }
  }
  const setSku = (i, k, v) => setP({
    ...p, skus: p.skus.map((s, j) => (j === i ? { ...s, [k]: v } : s)),
  })
  return (
    <Modal title={p.id ? `Edit — ${p.name}` : 'New custom product'} onClose={onClose} wide>
      <div className="row">
        <Field label="Category (product line)"><select value={categories.includes(p.category) ? p.category : 'Other'}
          onChange={(e) => setP({ ...p, category: e.target.value })}>
          {categories.map((c) => <option key={c}>{c}</option>)}</select></Field>
        <Field label="Group (optional set/brand)"><input style={{ width: 140 }} value={p.group} placeholder="e.g. set or brand"
          onChange={(e) => setP({ ...p, group: e.target.value })} /></Field>
        <Field label="Name"><input style={{ width: 240 }} value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} /></Field>
        <Field label="Type (physical kind)"><select value={p.item_type} onChange={(e) => setP({ ...p, item_type: e.target.value })}>
          {ITEM_TYPES.map((t) => <option key={t}>{t}</option>)}</select></Field>
        <Field label="UPC"><input style={{ width: 120 }} value={p.upc || ''} onChange={(e) => setP({ ...p, upc: e.target.value })} /></Field>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
        <b>Category</b> is the product line (mirrors TCGplayer). <b>Type</b> is the physical kind of thing
        (graded card, sealed box, accessory). e.g. a sealed Pokémon box = Category "Pokémon", Type "sealed".
      </p>
      <div className="row">
        <Field label="Description"><textarea rows={2} style={{ width: 500 }} value={p.description}
          onChange={(e) => setP({ ...p, description: e.target.value })} /></Field>
        <Field label="Image URLs (first = primary, one per line)"><textarea rows={2} style={{ width: 300 }}
          value={(p.images || []).join('\n')}
          onChange={(e) => setP({ ...p, images: e.target.value.split('\n').filter(Boolean) })} /></Field>
      </div>

      <h3>SKU variants</h3>
      {p.skus.map((s, i) => (
        <div className="row" key={i}>
          {isGraded ? (<>
            <Field label="Grader"><select value={s.grading_company || 'Raw'}
              onChange={(e) => setSku(i, 'grading_company', e.target.value)}>
              {GRADERS.map((g) => <option key={g}>{g}</option>)}</select></Field>
            <Field label="Grade"><input style={{ width: 60 }} value={s.grade_value || ''}
              onChange={(e) => setSku(i, 'grade_value', e.target.value)} /></Field>
            <Field label="Cert #"><input value={s.cert_number || ''}
              onChange={(e) => setSku(i, 'cert_number', e.target.value)} /></Field>
          </>) : (<>
            <Field label="Condition"><input style={{ width: 70 }} value={s.condition || ''}
              onChange={(e) => setSku(i, 'condition', e.target.value)} /></Field>
            <Field label="Printing"><input style={{ width: 90 }} value={s.printing || ''}
              onChange={(e) => setSku(i, 'printing', e.target.value)} /></Field>
            <Field label="Language"><input style={{ width: 60 }} value={s.language || ''}
              onChange={(e) => setSku(i, 'language', e.target.value)} /></Field>
          </>)}
          {!s.id && <button className="small danger" style={{ alignSelf: 'center' }}
            onClick={() => setP({ ...p, skus: p.skus.filter((_, j) => j !== i) })}>✕</button>}
        </div>))}
      <button className="small" onClick={() => setP({ ...p, skus: [...p.skus, {}] })}>+ SKU</button>

      {p.item_type === 'sealed' && (<>
        <h3>Breakdown components <span className="muted">(what one unit breaks into)</span></h3>
        {(p.breakdown_components || []).map((c, i) => (
          <div className="row" key={i}>
            <Field label="Component name"><input style={{ width: 240 }} value={c.name || ''}
              onChange={(e) => setP({
                ...p, breakdown_components: p.breakdown_components.map((x, j) => j === i ? { ...x, name: e.target.value } : x),
              })} /></Field>
            <Field label="Count"><input type="number" style={{ width: 60 }} value={c.count || 1}
              onChange={(e) => setP({
                ...p, breakdown_components: p.breakdown_components.map((x, j) => j === i ? { ...x, count: Number(e.target.value) } : x),
              })} /></Field>
            <button className="small danger" style={{ alignSelf: 'center' }}
              onClick={() => setP({ ...p, breakdown_components: p.breakdown_components.filter((_, j) => j !== i) })}>✕</button>
          </div>))}
        <button className="small" onClick={() => setP({
          ...p, breakdown_components: [...(p.breakdown_components || []), { name: '', count: 1 }],
        })}>+ component</button>
      </>)}

      <div className="row center" style={{ marginTop: 12 }}>
        <button className="primary" onClick={save}>Save product</button>
        <Msg msg={msg} />
      </div>
    </Modal>
  )
}

function BreakdownModal({ product, onClose }) {
  const [msg, ok, err] = useMsg()
  const [inventoryId, setInventoryId] = useState('')
  const [markup, setMarkup] = useState(20)
  return (
    <Modal title={`Break down — ${product.name}`} onClose={onClose}>
      <p className="muted">Deducts 1 sealed unit from the given inventory record and creates component records, carrying cost across proportionally.</p>
      <div className="row">
        <Field label="Inventory record ID (of the sealed item)"><input style={{ width: 110 }}
          value={inventoryId} onChange={(e) => setInventoryId(e.target.value)} /></Field>
        <Field label="Markup % for component prices"><input style={{ width: 70 }}
          value={markup} onChange={(e) => setMarkup(e.target.value)} /></Field>
        <button className="primary" onClick={async () => {
          try {
            const r = await api.post(`/api/custom/breakdown/${inventoryId}`, { markup_pct: Number(markup) })
            ok(`Created ${r.created.length} component record(s)`)
          } catch (e) { err(e) }
        }}>Break down 1 unit</button>
      </div>
      <Msg msg={msg} />
    </Modal>
  )
}

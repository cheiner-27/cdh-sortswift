import React, { useEffect, useState } from 'react'
import { api, download } from '../api.js'
import { Field, Msg, useMeta, useMsg } from '../components.jsx'

export default function ExportPage() {
  const meta = useMeta()
  const [msg, ok, err] = useMsg()
  const [columns, setColumns] = useState([])
  const [picked, setPicked] = useState([])
  const [layout, setLayout] = useState('native')
  const [fmt, setFmt] = useState('csv')
  const [excludeZero, setExcludeZero] = useState(true)
  const [mergeDupes, setMergeDupes] = useState(false)
  const [filter, setFilter] = useState({ game: '', set_code: '' })
  const [templates, setTemplates] = useState([])
  const [tplName, setTplName] = useState('')

  useEffect(() => {
    api.get('/api/exports/columns').then((cols) => {
      setColumns(cols)
      setPicked(cols.slice(0, 12).map((c) => c.key))
    })
    refreshTemplates()
  }, [])
  const refreshTemplates = () => api.get('/api/exports/templates').then(setTemplates)

  const move = (key, dir) => setPicked((p) => {
    const i = p.indexOf(key)
    if (i < 0 || i + dir < 0 || i + dir >= p.length) return p
    const n = [...p]; n.splice(i, 1); n.splice(i + dir, 0, key); return n
  })

  const body = () => ({
    filter: Object.fromEntries(Object.entries(filter).filter(([, v]) => v !== '')),
    columns: picked, layout, format: fmt,
    exclude_zero: excludeZero, merge_duplicates: mergeDupes,
  })

  const saveTemplate = async () => {
    try {
      await api.post('/api/exports/templates', {
        name: tplName, columns: picked, layout,
        options: { exclude_zero: excludeZero, merge_duplicates: mergeDupes },
      })
      ok('Template saved'); refreshTemplates()
    } catch (e) { err(e) }
  }
  const loadTemplate = (t) => {
    setPicked(t.columns); setLayout(t.layout)
    setExcludeZero(t.options?.exclude_zero ?? true)
    setMergeDupes(t.options?.merge_duplicates ?? false)
  }

  if (!meta) return null
  return (
    <div>
      <h2>Export</h2>
      <div className="panel">
        <div className="row">
          <Field label="Game"><select value={filter.game} onChange={(e) => setFilter({ ...filter, game: e.target.value })}>
            <option value="">all</option>{meta.games.map((g) => <option key={g}>{g}</option>)}</select></Field>
          <Field label="Set"><input style={{ width: 90 }} value={filter.set_code}
            onChange={(e) => setFilter({ ...filter, set_code: e.target.value })} /></Field>
          <Field label="Layout"><select value={layout} onChange={(e) => setLayout(e.target.value)}>
            <option value="native">native (column picker)</option>
            <option value="tcgplayer">TCGplayer</option>
            <option value="ebay">eBay</option></select></Field>
          <Field label="Format"><select value={fmt} onChange={(e) => setFmt(e.target.value)}>
            <option>csv</option><option>xlsx</option></select></Field>
        </div>
        <div className="row center">
          <label><input type="checkbox" checked={excludeZero} onChange={(e) => setExcludeZero(e.target.checked)} /> exclude zero-quantity rows</label>
          <label><input type="checkbox" checked={mergeDupes} onChange={(e) => setMergeDupes(e.target.checked)} /> merge duplicate SKUs</label>
          <button className="primary" onClick={() => download('/api/exports/inventory', body()).catch(err)}>Export inventory</button>
          <button onClick={() => download('/api/exports/out-of-stock', { format: fmt }).catch(err)}>Out-of-stock export</button>
        </div>
        {layout === 'tcgplayer' && <p className="muted" style={{ fontSize: 12 }}>
          The <b>TCGplayer</b> layout emits the exact columns of a TCGplayer "Pricing Custom Export"
          (TCGplayer Id, Product Line, Set Name, …, Total Quantity, Add to Quantity, TCG Marketplace Price,
          Photo URL). Since there's no TCGplayer API, this is how you push prices/quantities: re-upload it in
          Seller Hub. <b>Add to Quantity</b> is set to 0 (re-price only, don't add stock) and <b>TCG Marketplace
          Price</b> carries your price. Rows need a TCGplayer Id to match, so sync your catalog first.</p>}
        {layout === 'ebay' && <p className="muted" style={{ fontSize: 12 }}>
          The <b>eBay</b> layout is a simple SKU / title / condition / quantity / price sheet for bulk tools.</p>}
        <Msg msg={msg} />
      </div>

      {layout === 'native' && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Columns <span className="muted">(check to include, arrows to reorder)</span></h3>
          <table><tbody>
            {columns.map((c) => {
              const on = picked.includes(c.key)
              return (
                <tr key={c.key}>
                  <td style={{ width: 30 }}><input type="checkbox" checked={on}
                    onChange={() => setPicked((p) => on ? p.filter((k) => k !== c.key) : [...p, c.key])} /></td>
                  <td>{c.header} <span className="muted">({c.key})</span></td>
                  <td style={{ width: 120 }}>{on && <>
                    <span className="muted">#{picked.indexOf(c.key) + 1}</span>{' '}
                    <button className="small" onClick={() => move(c.key, -1)}>↑</button>{' '}
                    <button className="small" onClick={() => move(c.key, 1)}>↓</button></>}</td>
                </tr>)
            })}
          </tbody></table>
        </div>
      )}

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Saved templates</h3>
        <div className="row center">
          <input placeholder="template name" value={tplName} onChange={(e) => setTplName(e.target.value)} />
          <button disabled={!tplName} onClick={saveTemplate}>Save current as template</button>
        </div>
        <table><tbody>{templates.map((t) => (
          <tr key={t.id}>
            <td>{t.name}</td>
            <td className="muted">{t.layout} · {t.columns.length} col(s)</td>
            <td>
              <button className="small" onClick={() => loadTemplate(t)}>Load</button>{' '}
              <button className="small danger" onClick={async () => { await api.del(`/api/exports/templates/${t.id}`); refreshTemplates() }}>Delete</button>
            </td>
          </tr>))}</tbody></table>
      </div>
    </div>
  )
}

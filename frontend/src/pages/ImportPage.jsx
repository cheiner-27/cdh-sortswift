import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Field, Modal, Msg, SortTh, useMsg, useSort } from '../components.jsx'

export default function ImportPage() {
  const [msg, ok, err] = useMsg()
  const [fields, setFields] = useState([])
  const [preview, setPreview] = useState(null)   // upload result
  const [mapping, setMapping] = useState({})
  const [valueMaps, setValueMaps] = useState('') // JSON text
  const [mode, setMode] = useState('add')
  const [toStaging, setToStaging] = useState(true)
  const [mappedPreview, setMappedPreview] = useState(null)
  const [batches, setBatches] = useState([])
  const { sorted, sort, toggle } = useSort(batches)
  const [batchDetail, setBatchDetail] = useState(null)
  const [running, setRunning] = useState(false)

  useEffect(() => {
    api.get('/api/imports/system-fields').then(setFields)
    refreshBatches()
  }, [])
  const refreshBatches = () => api.get('/api/imports/batches').then(setBatches)

  const onFile = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    try {
      const p = await api.upload('/api/imports/preview', file)
      setPreview(p)
      // naive auto-mapping by header name
      const auto = {}
      for (const h of p.headers) {
        const lower = h.toLowerCase().replace(/[^a-z0-9]/g, '_')
        const hit = fields.find((f) => lower.includes(f) || f.includes(lower))
        auto[h] = hit || ''
      }
      setMapping(auto)
      setMappedPreview(null)
    } catch (e2) { err(e2) }
  }

  const parsedValueMaps = () => {
    if (!valueMaps.trim()) return null
    return JSON.parse(valueMaps)
  }

  const doMappedPreview = async () => {
    try {
      setMappedPreview(await api.post('/api/imports/preview-mapped', {
        file_b64: preview.file_b64, mapping, value_maps: parsedValueMaps(),
      }))
    } catch (e) { err(e) }
  }

  const run = async () => {
    setRunning(true)
    try {
      const batch = await api.post('/api/imports/run', {
        file_b64: preview.file_b64, filename: preview.filename,
        mapping, value_maps: parsedValueMaps(), mode, to_staging: toStaging,
      })
      ok(`Import ${batch.status}: ${batch.row_count} rows, ${batch.error_count} errors`)
      setBatchDetail(batch)
      setPreview(null)
      refreshBatches()
    } catch (e) { err(e) } finally { setRunning(false) }
  }

  const undo = async (id) => {
    try {
      const r = await api.post(`/api/imports/batches/${id}/undo`)
      ok(`Undone ${r.undone} row(s)` + (r.warnings.length ? ` — ${r.warnings.join('; ')}` : ''))
      refreshBatches()
    } catch (e) { err(e) }
  }

  return (
    <div>
      <h2>CSV Import</h2>
      <div className="panel">
        <div className="row center">
          <input type="file" accept=".csv" onChange={onFile} />
          <Field label="Mode"><select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="add">Add (increment quantities)</option>
            <option value="overwrite">Overwrite (set exact quantities)</option>
            <option value="deduction">Deduction (apply order export as decrements)</option>
          </select></Field>
          {mode === 'add' && <label><input type="checkbox" checked={toStaging}
            onChange={(e) => setToStaging(e.target.checked)} /> route through staging</label>}
        </div>
        <p className="muted">Rows need a stable identifier: Scryfall/catalog ID, TCGplayer product ID, or set code + collector number. Name-only rows queue for manual disambiguation.</p>
        <Msg msg={msg} />
      </div>

      {preview && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Column mapping — {preview.filename} ({preview.row_count} rows)</h3>
          <table><thead><tr><th>CSV column</th><th>Maps to</th><th>Sample values</th></tr></thead>
            <tbody>{preview.headers.map((h) => (
              <tr key={h}>
                <td>{h}</td>
                <td><select value={mapping[h] || ''} onChange={(e) => setMapping({ ...mapping, [h]: e.target.value })}>
                  <option value="">(ignore)</option>
                  {fields.map((f) => <option key={f}>{f}</option>)}
                </select></td>
                <td className="muted">{preview.sample_rows.slice(0, 3).map((r) => r[h]).filter(Boolean).join(' · ')}</td>
              </tr>))}</tbody></table>
          <h3>Value remapping <span className="muted">(optional, JSON)</span></h3>
          <p className="muted">{'e.g. {"printing": {"holo": "foil"}, "condition": {"Mint": "NM"}} — maps your legacy values to system-standard values per field.'}</p>
          <textarea rows={3} style={{ width: '100%' }} value={valueMaps}
            onChange={(e) => setValueMaps(e.target.value)} placeholder='{"printing": {"holo": "foil"}}' />
          <div className="row center" style={{ marginTop: 10 }}>
            <button onClick={doMappedPreview}>Preview mapped rows</button>
            <button className="primary" onClick={run} disabled={running}>{running ? 'Importing…' : 'Run import'}</button>
          </div>
          {mappedPreview && (
            <div className="table-wrap"><table>
              <thead><tr>{Object.keys(mappedPreview[0] || {}).map((k) => <th key={k}>{k}</th>)}</tr></thead>
              <tbody>{mappedPreview.map((r, i) => (
                <tr key={i}>{Object.keys(mappedPreview[0]).map((k) => <td key={k}>{String(r[k] ?? '')}</td>)}</tr>))}</tbody>
            </table></div>)}
        </div>
      )}

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Import history</h3>
        <table><thead><tr>
          <SortTh k="id" accessor={(b) => b.id} sort={sort} toggle={toggle}>ID</SortTh>
          <SortTh k="file" accessor={(b) => b.filename} sort={sort} toggle={toggle}>File</SortTh>
          <SortTh k="mode" accessor={(b) => b.mode} sort={sort} toggle={toggle}>Mode</SortTh>
          <SortTh k="status" accessor={(b) => b.status} sort={sort} toggle={toggle}>Status</SortTh>
          <SortTh k="rows" accessor={(b) => b.row_count} sort={sort} toggle={toggle}>Rows</SortTh>
          <SortTh k="qty" accessor={(b) => b.quantity_total} sort={sort} toggle={toggle}>Qty</SortTh>
          <SortTh k="errors" accessor={(b) => b.error_count} sort={sort} toggle={toggle}>Errors</SortTh>
          <SortTh k="when" accessor={(b) => b.created_at} sort={sort} toggle={toggle}>When</SortTh>
          <th></th></tr></thead>
          <tbody>{sorted.map((b) => (
            <tr key={b.id}>
              <td>{b.id}</td><td>{b.filename}</td><td>{b.mode}</td>
              <td><span className={`badge ${b.status === 'completed' ? 'green' : b.status === 'undone' ? '' : 'yellow'}`}>{b.status}</span></td>
              <td>{b.row_count}</td><td>{b.quantity_total}</td><td>{b.error_count}</td>
              <td className="muted">{b.created_at?.slice(0, 16).replace('T', ' ')}</td>
              <td>
                <button className="small" onClick={async () => setBatchDetail(await api.get(`/api/imports/batches/${b.id}`))}>Rows</button>{' '}
                {b.status !== 'undone' && <button className="small danger" onClick={() => undo(b.id)}>Undo</button>}
              </td>
            </tr>))}</tbody></table>
        <p className="muted">Undo is available for 15 minutes after import; it removes exactly the added quantities (clamped at 0).</p>
      </div>

      {batchDetail && <BatchModal batch={batchDetail} onClose={() => { setBatchDetail(null); refreshBatches() }} onMsg={{ ok, err }} />}
    </div>
  )
}

function BatchModal({ batch, onClose, onMsg }) {
  const [rows, setRows] = useState(batch.rows || [])
  const resolve = async (rowId, cardId) => {
    try {
      await api.post(`/api/imports/rows/${rowId}/resolve`, { card_id: cardId })
      setRows((rs) => rs.map((r) => (r.id === rowId ? { ...r, status: 'resolved' } : r)))
      onMsg.ok('Row resolved')
    } catch (e) { onMsg.err(e) }
  }
  return (
    <Modal title={`Import batch #${batch.id} — ${batch.filename}`} onClose={onClose} wide>
      <div className="table-wrap" style={{ maxHeight: 500, overflowY: 'auto' }}>
        <table><thead><tr><th>Status</th><th>Mapped</th><th>Error / disambiguation</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id}>
              <td><span className={`badge ${r.status === 'imported' || r.status === 'staged' ? 'green' : r.status === 'ambiguous' ? 'yellow' : r.status === 'error' ? 'red' : ''}`}>{r.status}</span></td>
              <td className="muted" style={{ fontSize: 12 }}>{JSON.stringify(r.mapped)}</td>
              <td>
                {r.error && <span className="error-text">{r.error}</span>}
                {r.status === 'ambiguous' && (
                  <div className="candidates-grid">
                    {r.candidates.map((c) => (
                      <div key={c.card_id} className="candidate" onClick={() => resolve(r.id, c.card_id)}>
                        {c.image_url && <img className="card-img" src={c.image_url} alt="" />}
                        <div style={{ fontSize: 12 }}>{c.name}</div>
                        <div className="muted" style={{ fontSize: 11 }}>{c.set_code} #{c.collector_number}</div>
                      </div>))}
                  </div>)}
              </td>
            </tr>))}</tbody></table>
      </div>
    </Modal>
  )
}

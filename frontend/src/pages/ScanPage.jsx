import React, { useEffect, useMemo, useState } from 'react'
import { api, download, fmtMoney, scanImageUrl } from '../api.js'
import { CardSearch, Field, Modal, Msg, useMeta, useMsg } from '../components.jsx'

const SORTS = {
  scan: (a, b) => a.seq - b.seq,
  value: (a, b) => (b.market_value || 0) - (a.market_value || 0),  // high value first (for sifting)
  name: (a, b) => (a.card?.name || '').localeCompare(b.card?.name || ''),
  file: (a, b) => a.file_name.localeCompare(b.file_name),
  confidence: (a, b) => a.confidence - b.confidence,
  collector: (a, b) => (a.card?.collector_number || '').localeCompare(b.card?.collector_number || '', undefined, { numeric: true }),
  rarity: (a, b) => (a.card?.rarity || '').localeCompare(b.card?.rarity || ''),
}

// Cards at/above this market value are worth pulling out of a bulk batch — shown
// bold-green so they pop while sifting; below it the price is muted.
const NOTABLE_VALUE = 2

export default function ScanPage() {
  const meta = useMeta()
  const [msg, ok, err] = useMsg()
  const [settings, setSettings] = useState(null)
  const [pulls, setPulls] = useState([])
  const [items, setItems] = useState([])
  const [activePull, setActivePull] = useState(null)
  const [selected, setSelected] = useState(new Set())
  const [sortKey, setSortKey] = useState('scan')
  const [flaggedOnly, setFlaggedOnly] = useState(false)
  const [detail, setDetail] = useState(null) // item shown in alternatives/manual modal
  const [pulling, setPulling] = useState(false)

  // Pull form state (session defaults, Section 2). Printing is deliberately not
  // a session default — it's a per-card property set during review.
  const [form, setForm] = useState({
    folder: '', game: 'mtg', use_subfolder_bins: false, pair_front_back: false,
    condition: 'NM', language: 'en', bin: '', cost: '',
  })

  useEffect(() => {
    api.get('/api/settings').then((s) => {
      setSettings(s)
      setForm((f) => ({ ...f, folder: s.scan_folder || '', ...s.session_defaults }))
    })
    refreshPulls()
  }, [])

  const refreshPulls = () => api.get('/api/scans/pulls').then(setPulls)
  const loadQueue = async (pullId) => {
    setActivePull(pullId)
    setSelected(new Set())
    const q = pullId ? `?pull_id=${pullId}` : ''
    setItems(await api.get(`/api/scans/queue${q}`))
  }

  const doPull = async () => {
    setPulling(true)
    try {
      const res = await api.post('/api/scans/pull', {
        folder: form.folder, game: form.game,
        use_subfolder_bins: form.use_subfolder_bins,
        pair_front_back: form.pair_front_back,
        session_defaults: {
          condition: form.condition,
          language: form.language, bin: form.bin,
          cost: form.cost === '' ? null : Number(form.cost),
        },
      })
      ok(`Pulled ${res.image_count} new image(s), ${res.items} queue item(s)`)
      await refreshPulls()
      await loadQueue(res.pull_id)
    } catch (e) { err(e) } finally { setPulling(false) }
  }

  const shown = useMemo(() => {
    let list = [...items]
    if (flaggedOnly) list = list.filter((i) => i.status === 'needs_review' || i.low_resolution)
    list.sort(SORTS[sortKey] || SORTS.scan)
    return list
  }, [items, sortKey, flaggedOnly])

  const toggle = (id) => setSelected((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
  })
  const toggleAll = () => setSelected((s) =>
    s.size === shown.length ? new Set() : new Set(shown.map((i) => i.id)))

  const patchItem = async (id, payload) => {
    const updated = await api.patch(`/api/scans/queue/${id}`, payload)
    setItems((list) => list.map((i) => (i.id === id ? updated : i)))
    return updated
  }

  const bulk = async (action, values) => {
    try {
      const res = await api.post('/api/scans/queue/bulk', {
        ids: [...selected], action, values, pull_id: activePull,
      })
      ok(`${action}: ${res.affected ?? res.rejected} item(s)`)
      await loadQueue(activePull)
    } catch (e) { err(e) }
  }

  const confirmOne = async (item) => {
    try {
      await api.post(`/api/scans/queue/${item.id}/confirm`)
      setItems((list) => list.filter((i) => i.id !== item.id))
      ok(`${item.card?.name || 'card'} → staging`)
    } catch (e) { err(e) }
  }
  const rejectOne = async (item) => {
    await api.post(`/api/scans/queue/${item.id}/reject`)
    setItems((list) => list.filter((i) => i.id !== item.id))
  }

  const [bulkVals, setBulkVals] = useState({})
  if (!meta) return null

  return (
    <div>
      <h2>Scan</h2>
      <div className="panel">
        <p className="muted" style={{ marginTop: 0, maxWidth: 900 }}>
          <b>Two-step workflow:</b> (1) point at a folder and <b>Pull Scans</b> — every image in it is
          hashed (duplicates skipped), recognized, and dropped into the review queue below; (2) you
          confirm each identified card, then it moves on to Staging. Files are never moved or renamed.
        </p>
        <div className="row">
          <Field label="Scan folder (full path to the folder of images)">
            <input style={{ width: 360 }} value={form.folder}
              placeholder="C:\Users\chrsh\Scans\2026-07-10"
              onChange={(e) => setForm({ ...form, folder: e.target.value })} /></Field>
          <Field label="Game"><select value={form.game} onChange={(e) => setForm({ ...form, game: e.target.value })}>
            {meta.games.map((g) => <option key={g}>{g}</option>)}</select></Field>
          <Field label="Session condition"><select value={form.condition} onChange={(e) => setForm({ ...form, condition: e.target.value })}>
            {meta.conditions.map((c) => <option key={c}>{c}</option>)}</select></Field>
          <Field label="Session language"><select value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })}>
            {meta.languages.map((l) => <option key={l}>{l}</option>)}</select></Field>
          <Field label="Session bin"><input style={{ width: 100 }} value={form.bin}
            onChange={(e) => setForm({ ...form, bin: e.target.value })} /></Field>
          <Field label="Unit cost ($)"><input style={{ width: 80 }} value={form.cost}
            onChange={(e) => setForm({ ...form, cost: e.target.value })} /></Field>
        </div>
        <div className="row center">
          <label title="Instead of the images directly in the folder, treat each immediate subfolder as its own bin and pull the images inside each.">
            <input type="checkbox" checked={form.use_subfolder_bins}
              onChange={(e) => setForm({ ...form, use_subfolder_bins: e.target.checked })} /> each subfolder is a bin</label>
          <label title="Treat consecutive image files as front/back pairs of one card (the second image becomes the card's back).">
            <input type="checkbox" checked={form.pair_front_back}
              onChange={(e) => setForm({ ...form, pair_front_back: e.target.checked })} /> pair front/back images</label>
          <button className="primary" onClick={doPull} disabled={pulling}>
            {pulling ? 'Pulling…' : 'Pull Scans'}</button>
          <Msg msg={msg} />
        </div>
        <p className="muted" style={{ fontSize: 12 }}>
          By default it reads the image files sitting <i>directly</i> in the folder above. Session
          condition / language / bin / cost are just defaults stamped on every card in this batch —
          you can override any of them per card in the review queue. Printing is set per card (foils
          and holos are mixed in a batch), so there's no session printing.
        </p>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Past pulls</h3>
        <div className="table-wrap"><table>
          <thead><tr><th>ID</th><th>Date</th><th>Folder</th><th>Images</th><th>Resolved</th><th>Pending</th><th></th></tr></thead>
          <tbody>{pulls.map((p) => (
            <tr key={p.id} style={activePull === p.id ? { background: 'rgba(79,156,249,0.1)' } : {}}>
              <td>{p.id}</td>
              <td>{p.pulled_at?.slice(0, 16).replace('T', ' ')}</td>
              <td className="muted">{p.folder}</td>
              <td>{p.image_count}</td><td>{p.resolved}</td><td>{p.pending}</td>
              <td className="row" style={{ marginBottom: 0 }}>
                <button className="small" onClick={() => loadQueue(p.id)}>Open</button>
                <button className="small" onClick={() => download(`/api/scans/export?pull_id=${p.id}&fmt=csv`)}>CSV</button>
                <button className="small" onClick={() => download(`/api/scans/export?pull_id=${p.id}&fmt=xlsx`)}>XLSX</button>
              </td>
            </tr>))}
          </tbody>
        </table></div>
      </div>

      {activePull && (
        <div className="panel">
          <div className="row center">
            <h3 style={{ margin: 0 }}>Review queue — pull #{activePull}</h3>
            <Field label="Sort"><select value={sortKey} onChange={(e) => setSortKey(e.target.value)}>
              <option value="scan">scan order</option><option value="value">market value (high→low)</option>
              <option value="name">card name</option>
              <option value="file">file name</option><option value="confidence">confidence</option>
              <option value="collector">collector #</option><option value="rarity">rarity</option>
            </select></Field>
            <label><input type="checkbox" checked={flaggedOnly}
              onChange={(e) => setFlaggedOnly(e.target.checked)} /> flagged only</label>
            <span style={{ flex: 1 }} />
            <span className="muted" title="Sum of each card's market value × quantity across the rows shown">
              {shown.length} shown · est. value <b style={{ color: 'var(--green)' }}>
                {fmtMoney(shown.reduce((s, i) => s + (i.market_value || 0) * i.quantity, 0))}</b>
            </span>
          </div>

          <div className="row center">
            <span className="muted">{selected.size} selected</span>
            <select onChange={(e) => setBulkVals({ ...bulkVals, condition: e.target.value })} defaultValue="">
              <option value="" disabled>condition…</option>
              {meta.conditions.map((c) => <option key={c}>{c}</option>)}</select>
            <select onChange={(e) => setBulkVals({ ...bulkVals, printing: e.target.value })} defaultValue="">
              <option value="" disabled>printing…</option>
              {meta.printings.map((p) => <option key={p}>{p}</option>)}</select>
            <select onChange={(e) => setBulkVals({ ...bulkVals, language: e.target.value })} defaultValue="">
              <option value="" disabled>language…</option>
              {meta.languages.map((l) => <option key={l}>{l}</option>)}</select>
            <input placeholder="bin…" style={{ width: 90 }}
              onChange={(e) => setBulkVals({ ...bulkVals, bin: e.target.value })} />
            <button className="small" disabled={!selected.size}
              onClick={() => bulk('set', bulkVals)}>Bulk set</button>
            <button className="small primary" disabled={!selected.size}
              onClick={() => bulk('approve')}>Bulk approve → staging</button>
            <button className="small danger" disabled={!selected.size}
              onClick={() => bulk('reject')}>Bulk reject</button>
            <button className="small danger"
              onClick={() => window.confirm('Discard the entire pulled batch?') && bulk('clear_all')}>Clear all</button>
          </div>

          <div className="table-wrap"><table>
            <thead><tr>
              <th><input type="checkbox" checked={selected.size === shown.length && shown.length > 0} onChange={toggleAll} /></th>
              <th>Scan</th><th>Match</th><th>Market</th><th>Confidence</th><th>Cond</th><th>Printing</th>
              <th>Lang</th><th>Bin</th><th>Qty</th><th></th>
            </tr></thead>
            <tbody>{shown.map((it) => (
              <tr key={it.id}>
                <td><input type="checkbox" checked={selected.has(it.id)} onChange={() => toggle(it.id)} /></td>
                <td>
                  <img className="card-img" src={scanImageUrl(it.image_path)} alt={it.file_name} />
                  <div className="muted" style={{ fontSize: 11 }}>{it.file_name}</div>
                  {it.low_resolution && <span className="badge red">low res</span>}
                </td>
                <td>
                  {it.card ? (<>
                    <div>{it.card.name}</div>
                    <div className="muted">{it.card.set_code} #{it.card.collector_number} · {it.card.rarity}</div>
                    {it.card.image_url && <img className="card-img" src={it.card.image_url} alt="" />}
                  </>) : <span className="badge red">no match</span>}
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  {it.market_value == null
                    ? <span className="muted" title="No TCGplayer price data for this card/printing">—</span>
                    : <b style={{ fontSize: 15, color: it.market_value >= NOTABLE_VALUE ? 'var(--green)' : 'var(--muted)' }}>
                        {fmtMoney(it.market_value)}</b>}
                  {it.quantity > 1 && it.market_value != null &&
                    <div className="muted" style={{ fontSize: 11 }}>×{it.quantity} = {fmtMoney(it.market_value * it.quantity)}</div>}
                </td>
                <td>
                  <span className={`badge ${it.status === 'needs_review' ? 'yellow' : 'green'}`}>
                    {(it.confidence * 100).toFixed(0)}% {it.method || '—'}</span>
                  <div><button className="small" style={{ marginTop: 4 }}
                    onClick={() => setDetail(it)}>alternatives</button></div>
                </td>
                <td><select value={it.condition} onChange={(e) => patchItem(it.id, { condition: e.target.value })}>
                  {meta.conditions.map((c) => <option key={c}>{c}</option>)}</select></td>
                <td><select value={it.printing} onChange={(e) => patchItem(it.id, { printing: e.target.value })}>
                  {meta.printings.map((p) => <option key={p}>{p}</option>)}</select></td>
                <td><select value={it.language} onChange={(e) => patchItem(it.id, { language: e.target.value })}>
                  {meta.languages.map((l) => <option key={l}>{l}</option>)}</select></td>
                <td><input style={{ width: 70 }} defaultValue={it.bin}
                  onBlur={(e) => e.target.value !== it.bin && patchItem(it.id, { bin: e.target.value })} /></td>
                <td><input type="number" min="1" style={{ width: 55 }} defaultValue={it.quantity}
                  onBlur={(e) => patchItem(it.id, { quantity: Number(e.target.value) || 1 })} /></td>
                <td>
                  <button className="small primary" disabled={!it.card} onClick={() => confirmOne(it)}>Confirm</button>{' '}
                  <button className="small danger" onClick={() => rejectOne(it)}>Reject</button>
                </td>
              </tr>))}
            </tbody>
          </table></div>
          {shown.length === 0 && <p className="muted">No pending items in this pull.</p>}
        </div>
      )}

      {detail && (
        <Modal title={`Alternatives — ${detail.file_name}`} onClose={() => setDetail(null)} wide>
          <div className="row">
            <img className="card-img large" src={scanImageUrl(detail.image_path)} alt="scan" />
            {detail.back_image_path &&
              <img className="card-img large" src={scanImageUrl(detail.back_image_path)} alt="back" />}
          </div>
          <h3>Top candidates</h3>
          <div className="candidates-grid">
            {(detail.candidates || []).map((c) => (
              <div key={c.card_id} className={`candidate ${detail.card_id === c.card_id ? 'selected' : ''}`}
                onClick={async () => { const u = await patchItem(detail.id, { card_id: c.card_id }); setDetail(u) }}>
                {c.image_url && <img className="card-img large" src={c.image_url} alt="" style={{ width: 150 }} />}
                <div>{c.name}</div>
                <div className="muted">{c.set_code} #{c.collector_number}</div>
                <span className="badge blue">{(c.score * 100).toFixed(0)}% {c.method}</span>
              </div>))}
            {(!detail.candidates || detail.candidates.length === 0) &&
              <span className="muted">No candidates — use manual search below.</span>}
          </div>
          <h3>Manual catalog search</h3>
          <CardSearch onSelect={async (c) => { const u = await patchItem(detail.id, { card_id: c.id }); setDetail(u) }} />
        </Modal>
      )}
    </div>
  )
}

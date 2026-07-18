import React, { useEffect, useState } from 'react'
import { api, download } from '../api.js'
import { Field, Msg, useMsg } from '../components.jsx'

export default function CycleCountPage() {
  const [msg, ok, err] = useMsg()
  const [bins, setBins] = useState([])
  const [counts, setCounts] = useState([])
  const [bin, setBin] = useState('')
  const [active, setActive] = useState(null)

  const refresh = () => {
    api.get('/api/inventory/bins').then(setBins)
    api.get('/api/inventory/cycle-counts/list').then(setCounts)
  }
  useEffect(refresh, [])

  const start = async () => {
    try {
      const r = await api.post('/api/inventory/cycle-counts', { bin })
      ok(`Started count #${r.count_id} (${r.lines} lines)`)
      open(r.count_id)
      refresh()
    } catch (e) { err(e) }
  }
  const open = async (id) => setActive(await api.get(`/api/inventory/cycle-counts/${id}`))

  const tally = async (line, counted) => {
    await api.patch(`/api/inventory/cycle-counts/lines/${line.id}`, {
      counted: counted === '' ? null : Number(counted),
    })
    open(active.id)
  }

  const approve = async () => {
    if (!window.confirm('Commit all discrepancies as inventory adjustments?')) return
    try {
      const r = await api.post(`/api/inventory/cycle-counts/${active.id}/approve`)
      ok(`Approved — ${r.adjusted} adjustment(s) committed, bin verified`)
      setActive(null); refresh()
    } catch (e) { err(e) }
  }

  const exportExpected = () => download('/api/exports/inventory', {
    filter: { bin: active.bin }, format: 'csv',
  }).catch(err)

  return (
    <div>
      <h2>Cycle Counts</h2>
      <p className="muted" style={{ maxWidth: 900 }}>
        A <b>cycle count</b> is a spot audit of one bin: you physically recount the cards in a single bin
        and compare them to what the system thinks is there, instead of re-counting your entire inventory
        at once. Pick a bin → the app lists every card it expects in that bin → you enter what you actually
        find → on <b>approve</b>, any differences are written as logged inventory adjustments. Nothing changes
        until you approve, and progress auto-saves so you can stop and resume. Use it to keep counts honest
        as miscounts, mis-pulls, and damage creep in over time.
      </p>
      <div className="row center">
        <Field label="Bin to count"><select value={bin} onChange={(e) => setBin(e.target.value)}>
          <option value="">choose…</option>
          {bins.map((b) => <option key={b.bin} value={b.bin === '(unassigned)' ? '' : b.bin}>{b.bin} ({b.units} units)</option>)}
        </select></Field>
        <button className="primary" disabled={bin === ''} onClick={start}>Start count</button>
        <Msg msg={msg} />
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Counts <span className="muted">(progress auto-saves; resume anytime)</span></h3>
        <table><thead><tr><th>ID</th><th>Bin</th><th>Status</th><th>Progress</th><th>Started</th><th></th></tr></thead>
          <tbody>{counts.map((c) => (
            <tr key={c.id}>
              <td>{c.id}</td><td>{c.bin || '(unassigned)'}</td>
              <td><span className={`badge ${c.status === 'completed' ? 'green' : 'yellow'}`}>{c.status}</span></td>
              <td>{c.counted}/{c.lines}</td>
              <td className="muted">{c.created_at?.slice(0, 16).replace('T', ' ')}</td>
              <td>{c.status === 'in_progress' && <button className="small" onClick={() => open(c.id)}>Resume</button>}</td>
            </tr>))}</tbody></table>
      </div>

      {active && (
        <div className="panel">
          <div className="row center">
            <h3 style={{ margin: 0 }}>Counting bin "{active.bin || '(unassigned)'}"</h3>
            <span style={{ flex: 1 }} />
            <button onClick={exportExpected}>Export expected (offline count)</button>
            <button className="primary" onClick={approve}>Review & approve</button>
          </div>
          <table><thead><tr><th></th><th>Item</th><th>Cond</th><th>Printing</th>
            <th>Expected</th><th>Counted</th></tr></thead>
            <tbody>{active.lines.map((l) => (
              <tr key={l.id}>
                <td><span className={`badge ${l.status === 'match' ? 'green' : l.status === 'discrepancy' ? 'yellow' : 'red'}`}>
                  {l.status === 'match' ? '✓' : l.status === 'discrepancy' ? 'Δ' : '—'}</span></td>
                <td>{l.name}</td><td>{l.condition}</td><td>{l.printing}</td>
                <td>{l.expected}</td>
                <td><input type="number" min="0" style={{ width: 70 }} defaultValue={l.counted ?? ''}
                  onBlur={(e) => tally(l, e.target.value)} /></td>
              </tr>))}</tbody></table>
          <p className="muted">Nothing changes inventory until you approve. Green = matches, yellow = discrepancy, red = uncounted.</p>
        </div>
      )}
    </div>
  )
}

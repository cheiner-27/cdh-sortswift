import React, { useEffect, useMemo, useState } from 'react'
import { api } from './api.js'

// --- Sortable tables -------------------------------------------------------
// useSort(rows) returns the rows sorted by the active column, plus `sort`
// state and a `toggle(key, accessor)` cycling asc -> desc -> off. Pair with
// <SortTh> headers. Values compare numerically when both are numbers, else as
// natural-ordered strings (so "16/101" and dates sort sensibly).
export function useSort(rows, initial = null) {
  const [sort, setSort] = useState(initial) // { key, dir, fn } | null
  const sorted = useMemo(() => {
    if (!sort || !sort.fn) return rows
    const arr = [...(rows || [])]
    arr.sort((a, b) => {
      const av = sort.fn(a), bv = sort.fn(b)
      const aEmpty = av === null || av === undefined || av === ''
      const bEmpty = bv === null || bv === undefined || bv === ''
      if (aEmpty && bEmpty) return 0
      if (aEmpty) return 1              // blanks always sort last
      if (bEmpty) return -1
      let c
      if (typeof av === 'number' && typeof bv === 'number') c = av - bv
      else c = String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: 'base' })
      return sort.dir === 'desc' ? -c : c
    })
    return arr
  }, [rows, sort])
  const toggle = (key, fn) => setSort((s) =>
    !s || s.key !== key ? { key, fn, dir: 'asc' }
      : s.dir === 'asc' ? { key, fn, dir: 'desc' } : null)
  return { sorted, sort, toggle }
}

// Clickable, sort-indicating <th>. `k` is the column key, `accessor` maps a row
// to its sort value. Pass the `sort`/`toggle` from useSort.
export function SortTh({ k, accessor, sort, toggle, children, ...rest }) {
  const active = sort && sort.key === k
  return (
    <th onClick={() => toggle(k, accessor)}
      style={{ cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }} {...rest}>
      {children}{' '}
      <span style={{ opacity: active ? 0.9 : 0.3, fontSize: 10 }}>
        {active ? (sort.dir === 'asc' ? '▲' : '▼') : '↕'}</span>
    </th>
  )
}

export function Modal({ title, onClose, children, wide }) {
  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={wide ? { minWidth: 800 } : {}}>
        <div className="row center" style={{ justifyContent: 'space-between' }}>
          <h3>{title}</h3>
          <button className="small" onClick={onClose}>✕</button>
        </div>
        {children}
      </div>
    </div>
  )
}

export function Field({ label, children }) {
  return <label className="field">{label}{children}</label>
}

// A <select> whose last option is "➕ Add new…"; picking it swaps in a text
// input so a value outside the list can be entered. The current value is always
// selectable even if it isn't in `options` (e.g. a previously-added one).
export function DropdownWithAdd({ value, onChange, options = [], width = 150,
  placeholder = '—', allowBlank = true }) {
  const [adding, setAdding] = useState(false)
  const opts = [...new Set([...options, ...(value && !options.includes(value) ? [value] : [])])]
  if (adding) {
    return (
      <input autoFocus style={{ width }} placeholder="type new value…" defaultValue=""
        onBlur={(e) => { const v = e.target.value.trim(); if (v) onChange(v); setAdding(false) }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.target.blur()
          else if (e.key === 'Escape') setAdding(false)
        }} />
    )
  }
  return (
    <select style={{ width }} value={value || ''} onChange={(e) => {
      if (e.target.value === '__add__') setAdding(true)
      else onChange(e.target.value)
    }}>
      {allowBlank && <option value="">{placeholder}</option>}
      {opts.map((o) => <option key={o} value={o}>{o}</option>)}
      <option value="__add__">➕ Add new…</option>
    </select>
  )
}

export function Msg({ msg }) {
  if (!msg) return null
  return <div className={msg.error ? 'error-text' : 'success-text'}>{msg.text}</div>
}

export function useMsg() {
  const [msg, setMsg] = useState(null)
  const ok = (text) => setMsg({ text })
  const err = (e) => setMsg({ text: String(e.message || e), error: true })
  return [msg, ok, err]
}

export function useMeta() {
  const [meta, setMeta] = useState(null)
  useEffect(() => { api.get('/api/meta').then(setMeta).catch(() => {}) }, [])
  return meta
}

// Catalog card search box with dropdown results.
// clearOnSelect: wipe results after picking (pick-mode); false keeps the list
// for browsing. selectLabel: text on the action button.
export function CardSearch({ onSelect, game, clearOnSelect = true, selectLabel = 'Select' }) {
  const [q, setQ] = useState('')
  const [setCode, setSetCode] = useState('')
  const [num, setNum] = useState('')
  const [results, setResults] = useState([])
  const search = async () => {
    if (!q.trim() && !setCode.trim() && !num.trim()) return
    const p = new URLSearchParams()
    if (q.trim()) p.set('q', q.trim())
    if (setCode.trim()) p.set('set_code', setCode.trim())
    if (num.trim()) p.set('collector_number', num.trim())
    if (game) p.set('game', game)
    p.set('limit', '75')
    setResults(await api.get(`/api/catalog/search?${p}`))
  }
  const onKey = (e) => e.key === 'Enter' && search()
  return (
    <div>
      <div className="row">
        <input placeholder="card name" value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKey} style={{ width: 200 }} />
        <input placeholder="set code" value={setCode} onChange={(e) => setSetCode(e.target.value)}
          onKeyDown={onKey} style={{ width: 90 }} title="e.g. 3ED, TOTC — filter to (or browse) a set" />
        <input placeholder="#" value={num} onChange={(e) => setNum(e.target.value)}
          onKeyDown={onKey} style={{ width: 60 }} title="collector number" />
        <button onClick={search}>Search catalog</button>
      </div>
      {results.length > 0 && (
        <div className="table-wrap" style={{ maxHeight: 300, overflowY: 'auto' }}>
          <table>
            <thead><tr><th></th><th>Name</th><th>Set</th><th>#</th><th>Rarity</th><th></th></tr></thead>
            <tbody>
              {results.map((c) => (
                <tr key={c.id}>
                  <td>{c.image_url && <img className="card-img" src={c.image_url} alt="" />}</td>
                  <td>{c.name}</td>
                  <td>{c.set_code} <span className="muted">{c.set_name}</span></td>
                  <td>{c.collector_number}</td>
                  <td>{c.rarity}</td>
                  <td><button className="small primary" onClick={() => { onSelect(c); if (clearOnSelect) setResults([]) }}>{selectLabel}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// Print helper: renders children into a .print-area then calls window.print()
export function PrintArea({ children }) {
  return <div className="print-area">{children}</div>
}

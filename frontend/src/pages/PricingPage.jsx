import React, { useEffect, useState } from 'react'
import { api, fmtMoney } from '../api.js'
import { Field, Msg, useMeta, useMsg } from '../components.jsx'

const SOURCE_LABELS = {
  tcg_market: 'TCG Market', tcg_mid: 'TCG Mid',
  tcg_low: 'TCG Low', tcg_direct_low: 'TCG Direct Low',
}

export default function PricingPage() {
  const meta = useMeta()
  const [msg, ok, err] = useMsg()
  const [game, setGame] = useState('mtg')
  const [config, setConfig] = useState(null)
  const [repriceMk, setRepriceMk] = useState('ebay')
  const [sim, setSim] = useState(null)
  const [simLargeOnly, setSimLargeOnly] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.get(`/api/pricing/config/${game}`).then((c) => setConfig(structuredClone(c)))
    setSim(null)
  }, [game])

  const save = async () => {
    try { await api.put(`/api/pricing/config/${game}`, config); ok('Rules saved') }
    catch (e) { err(e) }
  }
  const simulate = async () => {
    setBusy(true)
    try { setSim(await api.post(`/api/pricing/simulate/${repriceMk}`, { filter: { game } })) }
    catch (e) { err(e) } finally { setBusy(false) }
  }
  const apply = async () => {
    if (!window.confirm(`Reprice all in-stock ${game} inventory for ${repriceMk}?`)) return
    setBusy(true)
    try {
      const r = await api.post(`/api/pricing/apply/${repriceMk}`, { filter: { game } })
      ok(`Repriced: ${r.updated} updated, ${r.skipped} skipped`); setSim(null)
    } catch (e) { err(e) } finally { setBusy(false) }
  }

  const setTier = (i, fn) => setConfig((c) => { const n = structuredClone(c); fn(n.tiers[i]); return n })
  const addTier = () => setConfig((c) => {
    const n = structuredClone(c)
    const last = n.tiers[n.tiers.length - 1]
    if (last && last.max === null) last.max = (last.min || 0) + 5
    const off = {}; meta.marketplaces.forEach((m) => { off[m] = { pct: 0, flat: 0 } })
    n.tiers.push({
      name: `tier ${n.tiers.length + 1}`, min: last ? last.max : 0, max: null,
      modifiers: { condition: { NM: 100, LP: 85, MP: 70, HP: 50, DMG: 30 }, printing: {}, language: {}, age_decay: { days: 0, pct: 0 } },
      offsets: off,
      guards: { max_move_pct: null, tier_lock: { up: false, down: false }, rarity_floors: {}, cost_floor: true },
      rounding: '0.01',
    })
    return n
  })

  if (!meta || !config) return null
  const shownSim = sim ? sim.results.filter((r) => !simLargeOnly || r.large_move) : []
  const unusedSources = (meta.price_sources || []).filter((s) => !config.sources.includes(s))

  return (
    <div>
      <h2>Pricing <span className="muted">— rules per game</span></h2>
      <div className="tabs">
        {meta.games.map((g) => (
          <button key={g} className={g === game ? 'active' : ''} onClick={() => setGame(g)}>{g}</button>))}
      </div>

      <p className="muted" style={{ maxWidth: 900 }}>
        For each price <b>tier</b> (a band of the card's current price) the engine picks a baseline
        source, multiplies the stacking modifiers (condition × printing × language × age), applies the
        per-platform offset, enforces the guards, then rounds. Full walk-through in <b>Help → Pricing rules</b>.
      </p>

      {/* --- Price sources (ordered fallback) --- */}
      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Baseline price — fallback order</h3>
        <p className="muted">Top to bottom: the first source that has a value is used as the baseline.</p>
        <table style={{ width: 'auto' }}><tbody>
          {config.sources.map((s, i) => (
            <tr key={s}>
              <td>{i === 0 && <span className="badge blue">primary</span>}</td>
              <td style={{ minWidth: 130 }}>{SOURCE_LABELS[s] || s}</td>
              <td>
                <button className="small" disabled={i === 0} onClick={() => setConfig((c) => {
                  const n = structuredClone(c);[n.sources[i - 1], n.sources[i]] = [n.sources[i], n.sources[i - 1]]; return n
                })}>↑</button>{' '}
                <button className="small" disabled={i === config.sources.length - 1} onClick={() => setConfig((c) => {
                  const n = structuredClone(c);[n.sources[i + 1], n.sources[i]] = [n.sources[i], n.sources[i + 1]]; return n
                })}>↓</button>{' '}
                <button className="small danger" disabled={config.sources.length <= 1}
                  onClick={() => setConfig((c) => ({ ...c, sources: c.sources.filter((x) => x !== s) }))}>✕</button>
              </td>
            </tr>))}
        </tbody></table>
        {unusedSources.length > 0 && (
          <div className="row center">
            <span className="muted">Add source:</span>
            {unusedSources.map((s) => (
              <button key={s} className="small" onClick={() => setConfig((c) => ({ ...c, sources: [...c.sources, s] }))}>+ {SOURCE_LABELS[s] || s}</button>))}
          </div>)}
      </div>

      {/* --- Tiers --- */}
      {config.tiers.map((tier, i) => (
        <div className="panel" key={i}>
          <div className="row center">
            <Field label="Tier name"><input style={{ width: 110 }} value={tier.name}
              onChange={(e) => setTier(i, (t) => { t.name = e.target.value })} /></Field>
            <Field label="Current price ≥ $"><input style={{ width: 70 }} value={tier.min ?? 0}
              onChange={(e) => setTier(i, (t) => { t.min = Number(e.target.value) || 0 })} /></Field>
            <Field label="< $ (blank = open)"><input style={{ width: 70 }} value={tier.max ?? ''}
              onChange={(e) => setTier(i, (t) => { t.max = e.target.value === '' ? null : Number(e.target.value) })} /></Field>
            <Field label="Rounding"><select value={tier.rounding}
              onChange={(e) => setTier(i, (t) => { t.rounding = e.target.value })}>
              {(meta.rounding_options || []).map((r) => <option key={r} value={r}>{r === '1' ? 'nearest $1' : r}</option>)}</select></Field>
            <span style={{ flex: 1 }} />
            {config.tiers.length > 1 &&
              <button className="small danger" onClick={() => setConfig((c) => {
                const n = structuredClone(c); n.tiers.splice(i, 1); return n
              })}>Remove tier</button>}
          </div>

          <div className="row" style={{ gap: 28, alignItems: 'flex-start' }}>
            <div>
              <h3>Modifiers <span className="muted">(stack ×)</span></h3>
              <ModTable title="Condition %" keys={meta.conditions} table={tier.modifiers.condition} dflt="100"
                onChange={(k, v) => setTier(i, (t) => { v === '' ? delete t.modifiers.condition[k] : t.modifiers.condition[k] = Number(v) })} />
            </div>
            <ModTable title="Printing %" keys={meta.printings} table={tier.modifiers.printing}
              onChange={(k, v) => setTier(i, (t) => { v === '' ? delete t.modifiers.printing[k] : t.modifiers.printing[k] = Number(v) })} />
            <ModTable title="Language %" keys={meta.languages} table={tier.modifiers.language}
              onChange={(k, v) => setTier(i, (t) => { v === '' ? delete t.modifiers.language[k] : t.modifiers.language[k] = Number(v) })} />
            <div>
              <h3>Age decay</h3>
              <Field label="after days in stock"><input style={{ width: 60 }} value={tier.modifiers.age_decay.days}
                onChange={(e) => setTier(i, (t) => { t.modifiers.age_decay.days = Number(e.target.value) || 0 })} /></Field>
              <Field label="reduce %"><input style={{ width: 60 }} value={tier.modifiers.age_decay.pct}
                onChange={(e) => setTier(i, (t) => { t.modifiers.age_decay.pct = Number(e.target.value) || 0 })} /></Field>

              <h3 style={{ marginTop: 16 }}>Offset by platform</h3>
              {meta.marketplaces.map((mk) => (
                <div className="row center" key={mk} style={{ marginBottom: 4 }}>
                  <span style={{ width: 74, fontSize: 12 }}>{mk}</span>
                  <input title="percent" style={{ width: 55 }} value={tier.offsets[mk]?.pct ?? 0}
                    onChange={(e) => setTier(i, (t) => { (t.offsets[mk] ||= { pct: 0, flat: 0 }).pct = Number(e.target.value) || 0 })} /><span className="muted">%</span>
                  <input title="flat $" style={{ width: 55 }} value={tier.offsets[mk]?.flat ?? 0}
                    onChange={(e) => setTier(i, (t) => { (t.offsets[mk] ||= { pct: 0, flat: 0 }).flat = Number(e.target.value) || 0 })} /><span className="muted">$</span>
                </div>))}
            </div>

            <div>
              <h3>Guards</h3>
              <label><input type="checkbox" checked={tier.guards.cost_floor}
                onChange={(e) => setTier(i, (t) => { t.guards.cost_floor = e.target.checked })} /> never below FIFO cost</label>
              <div style={{ marginTop: 8 }}>
                <Field label="max move % per reprice"><input style={{ width: 60 }} value={tier.guards.max_move_pct ?? ''}
                  onChange={(e) => setTier(i, (t) => { t.guards.max_move_pct = e.target.value === '' ? null : Number(e.target.value) })} /></Field>
              </div>
              <div style={{ marginTop: 8 }}>
                <span className="muted" style={{ fontSize: 12 }}>Tier-movement lock (keep price in band):</span><br />
                <label><input type="checkbox" checked={tier.guards.tier_lock?.up || false}
                  onChange={(e) => setTier(i, (t) => { (t.guards.tier_lock ||= {}).up = e.target.checked })} /> can't move up</label>{' '}
                <label><input type="checkbox" checked={tier.guards.tier_lock?.down || false}
                  onChange={(e) => setTier(i, (t) => { (t.guards.tier_lock ||= {}).down = e.target.checked })} /> can't move down</label>
              </div>
              <h3 style={{ marginTop: 12 }}>Rarity floors $</h3>
              <div style={{ maxHeight: 160, overflowY: 'auto' }}>
                {meta.rarities.map((r) => (
                  <div className="row center" key={r} style={{ marginBottom: 3 }}>
                    <span style={{ width: 90, fontSize: 12 }}>{r}</span>
                    <input style={{ width: 55 }} value={tier.guards.rarity_floors[r] ?? ''}
                      onChange={(e) => setTier(i, (t) => {
                        e.target.value === '' ? delete t.guards.rarity_floors[r]
                          : t.guards.rarity_floors[r] = Number(e.target.value)
                      })} />
                  </div>))}
              </div>
            </div>
          </div>
        </div>
      ))}

      <div className="panel">
        <div className="row center">
          <button onClick={addTier}>+ Add tier</button>
          <span className="muted">Tiers band on the card's current price; they must not overlap and only the last may be open-ended.</span>
        </div>
        <h3>Scope overrides <span className="muted">(advanced, JSON)</span></h3>
        <div className="row">
          <Field label='set_overrides — e.g. {"MH3": {"suppress": true}}'>
            <textarea rows={2} style={{ width: 380 }}
              defaultValue={JSON.stringify(config.set_overrides || {})}
              onBlur={(e) => { try { setConfig((c) => ({ ...c, set_overrides: JSON.parse(e.target.value || '{}') })) } catch { err(new Error('bad JSON in set_overrides')) } }} />
          </Field>
          <Field label='card_overrides — e.g. {"123": {"fixed_price": 5}}'>
            <textarea rows={2} style={{ width: 380 }}
              defaultValue={JSON.stringify(config.card_overrides || {})}
              onBlur={(e) => { try { setConfig((c) => ({ ...c, card_overrides: JSON.parse(e.target.value || '{}') })) } catch { err(new Error('bad JSON in card_overrides')) } }} />
          </Field>
        </div>
        <div className="row center">
          <button className="primary" onClick={save}>Save {game} rules</button>
          <Msg msg={msg} />
        </div>
      </div>

      {/* --- Reprice --- */}
      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Reprice {game} inventory</h3>
        <div className="row center">
          <Field label="Platform"><select value={repriceMk} onChange={(e) => setRepriceMk(e.target.value)}>
            {meta.marketplaces.map((m) => <option key={m}>{m}</option>)}</select></Field>
          <button onClick={simulate} disabled={busy}>{busy ? 'Working…' : 'Simulate (preview)'}</button>
          <button className="danger" onClick={apply} disabled={busy}>Reprice now</button>
          <span className="muted">Manual trigger only — nothing reprices on a schedule.</span>
        </div>
      </div>

      {sim && (
        <div className="panel">
          <div className="row center">
            <h3 style={{ margin: 0 }}>Simulation — {sim.count} item(s) on {repriceMk}</h3>
            <label><input type="checkbox" checked={simLargeOnly}
              onChange={(e) => setSimLargeOnly(e.target.checked)} /> large moves only</label>
          </div>
          <div className="table-wrap" style={{ maxHeight: 500, overflowY: 'auto' }}>
            <table><thead><tr><th>Item</th><th>Current</th><th>New</th><th>Move</th><th>Status</th><th>Trace</th></tr></thead>
              <tbody>{shownSim.map((r) => (
                <tr key={r.inventory_id}>
                  <td>{r.description}</td>
                  <td>{fmtMoney(r.old_price)}</td>
                  <td>{fmtMoney(r.new_price)}</td>
                  <td>{r.move_pct !== null && <span className={`badge ${r.large_move ? 'red' : 'green'}`}>{r.move_pct > 0 ? '+' : ''}{r.move_pct}%</span>}</td>
                  <td><span className={`badge ${r.status === 'ok' ? '' : 'yellow'}`}>{r.status}</span></td>
                  <td className="muted" style={{ fontSize: 11 }}>{r.trace.join(' → ')}</td>
                </tr>))}</tbody></table>
          </div>
        </div>
      )}
    </div>
  )
}

function ModTable({ title, keys, table, onChange, dflt = '' }) {
  return (
    <div>
      <h3>{title}</h3>
      {keys.map((k) => (
        <div className="row center" key={k} style={{ marginBottom: 3 }}>
          <span style={{ width: 90, fontSize: 12 }}>{k}</span>
          <input style={{ width: 55 }} value={table[k] ?? ''} placeholder={dflt || '100'}
            onChange={(e) => onChange(k, e.target.value)} />
        </div>))}
    </div>
  )
}

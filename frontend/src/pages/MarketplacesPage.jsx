import React, { useEffect, useState } from 'react'
import { api, download } from '../api.js'
import { Field, Modal, Msg, useMeta, useMsg } from '../components.jsx'

export default function MarketplacesPage() {
  const meta = useMeta()
  const [msg, ok, err] = useMsg()
  const [accounts, setAccounts] = useState([])
  const [rules, setRules] = useState([])
  const [errors, setErrors] = useState([])
  const [editRule, setEditRule] = useState(null)
  const [credsFor, setCredsFor] = useState(null)
  const [busy, setBusy] = useState(false)

  const refresh = () => {
    api.get('/api/marketplaces/accounts').then(setAccounts)
    api.get('/api/marketplaces/rules').then(setRules)
    api.get('/api/marketplaces/errors').then(setErrors)
  }
  useEffect(refresh, [])

  const setStatus = async (mk, status) => {
    if (status === 'disconnected' &&
      !window.confirm('Disconnect revokes stored credentials and stops all automation. Continue?')) return
    await api.put(`/api/marketplaces/accounts/${mk}`, { status })
    refresh()
  }

  const op = async (mk, name) => {
    if (name === 'rebuild' &&
      !window.confirm('Rebuild ends existing listings and re-pushes them. Only for broken listing rules. Continue?')) return
    setBusy(true)
    try {
      const r = await api.post(`/api/marketplaces/${mk}/${name}`, {})
      if (r.error) err(new Error(r.error))
      else ok(`${mk} ${name}: ${JSON.stringify(r)}`)
      refresh()
    } catch (e) { err(e) } finally { setBusy(false) }
  }

  if (!meta) return null
  return (
    <div>
      <h2>Marketplaces</h2>
      <Msg msg={msg} />
      {accounts.map((a) => (
        <div className="panel" key={a.marketplace}>
          <div className="row center">
            <h3 style={{ margin: 0 }}>{a.marketplace}</h3>
            <span className={`badge ${a.status === 'connected' ? 'green' : a.status === 'paused' ? 'yellow' : ''}`}>{a.status}</span>
            {a.dry_run && <span className="badge blue">dry run</span>}
            <span style={{ flex: 1 }} />
            <button onClick={() => setCredsFor(a)}>Credentials…</button>
            {a.status !== 'connected' && <button onClick={() => setStatus(a.marketplace, 'connected')}>Connect</button>}
            {a.status === 'connected' && <button onClick={() => setStatus(a.marketplace, 'paused')}>Pause</button>}
            {a.status === 'paused' && <button onClick={() => setStatus(a.marketplace, 'connected')}>Resume</button>}
            {a.status !== 'disconnected' && <button className="danger" onClick={() => setStatus(a.marketplace, 'disconnected')}>Disconnect</button>}
          </div>
          <p className="muted">
            Pause keeps credentials and existing listings live but stops auto-push/reprice-push.
            {a.marketplace === 'ebay' && <> Order polling every {a.poll_interval_minutes} min while connected
              {a.last_order_poll_at && <> · last poll {a.last_order_poll_at.slice(0, 16).replace('T', ' ')}</>}.</>}
          </p>
          <div className="row center">
            <Field label="Poll interval (min)"><input style={{ width: 60 }} defaultValue={a.poll_interval_minutes}
              onBlur={(e) => api.put(`/api/marketplaces/accounts/${a.marketplace}`, { poll_interval_minutes: Number(e.target.value) || 10 })} /></Field>
            <label><input type="checkbox" defaultChecked={a.auto_push_on_add}
              onChange={(e) => api.put(`/api/marketplaces/accounts/${a.marketplace}`, { auto_push_on_add: e.target.checked })} /> auto-push on add</label>
            <span style={{ flex: 1 }} />
            <button disabled={busy} onClick={() => op(a.marketplace, 'resync')}>Resync (changed)</button>
            <button disabled={busy} onClick={() => op(a.marketplace, 'push-remaining')}>Push Remaining</button>
            <button disabled={busy} onClick={() => op(a.marketplace, 'clear-ids')}>Clear Listing IDs</button>
            <button disabled={busy} className="danger" onClick={() => op(a.marketplace, 'rebuild')}>Rebuild</button>
            {a.marketplace === 'ebay' &&
              <button disabled={busy} onClick={() => op(a.marketplace, 'poll-orders')}>Sync orders now</button>}
          </div>
          {a.marketplace === 'tcgplayer' && (
            <div className="row center" style={{ marginTop: 8 }}>
              <span className="muted">CSV fallback:</span>
              <button onClick={() => download('/api/marketplaces/tcgplayer/export-listing-csv', {}).catch(err)}>Export listing CSV</button>
              <button onClick={() => download('/api/marketplaces/tcgplayer/export-deduction-csv', {}).catch(err)}>Export deduction CSV (eBay sales)</button>
            </div>)}
        </div>
      ))}

      <div className="panel">
        <div className="row center">
          <h3 style={{ margin: 0 }}>Listing rules</h3>
          <span className="muted">first-match-wins by priority; unmatched items become listing errors</span>
          <span style={{ flex: 1 }} />
          <button onClick={() => setEditRule({ marketplace: 'ebay', name: '', priority: rules.length, active: true, filters: {}, condition_allowlist: [], best_offer: {} })}>+ New rule</button>
        </div>
        <table><thead><tr><th>Priority</th><th>Marketplace</th><th>Name</th><th>Filters</th><th>Conditions</th><th>Active</th><th></th></tr></thead>
          <tbody>{rules.map((r) => (
            <tr key={r.id}>
              <td>{r.priority}</td><td>{r.marketplace}</td><td>{r.name}</td>
              <td className="muted" style={{ fontSize: 12 }}>{JSON.stringify(r.filters)}</td>
              <td>{r.condition_allowlist?.length ? r.condition_allowlist.join(',') : 'all'}
                {r.block_sealed && <span className="badge"> no sealed</span>}
                {r.block_singles && <span className="badge"> no singles</span>}</td>
              <td>{r.active ? <span className="badge green">on</span> : <span className="badge">off</span>}</td>
              <td>
                <button className="small" onClick={() => setEditRule(r)}>Edit</button>{' '}
                <button className="small danger" onClick={async () => { await api.del(`/api/marketplaces/rules/${r.id}`); refresh() }}>Del</button>
              </td>
            </tr>))}</tbody></table>
      </div>

      {errors.length > 0 && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Listing errors <span className="badge red">{errors.length}</span></h3>
          <p className="muted">Errored items are excluded from bulk sync until re-attempted individually (Inventory → Detail → Sync now).</p>
          <table><thead><tr><th>Marketplace</th><th>Item</th><th>Code</th><th>Message</th></tr></thead>
            <tbody>{errors.map((e) => (
              <tr key={e.listing_id}>
                <td>{e.marketplace}</td>
                <td>{e.item?.card?.name || e.item?.custom_name || `#${e.item?.id}`}</td>
                <td><span className="badge red">{e.error_code}</span></td>
                <td className="muted">{e.error_message}</td>
              </tr>))}</tbody></table>
        </div>
      )}

      {editRule && <RuleModal meta={meta} rule={editRule} onClose={() => { setEditRule(null); refresh() }} />}
      {credsFor && <CredsModal account={credsFor} onClose={() => { setCredsFor(null); refresh() }} />}
    </div>
  )
}

function RuleModal({ meta, rule, onClose }) {
  const [msg, ok, err] = useMsg()
  const [r, setR] = useState(structuredClone(rule))
  const save = async () => {
    try {
      if (r.id) await api.put(`/api/marketplaces/rules/${r.id}`, r)
      else await api.post('/api/marketplaces/rules', r)
      ok('Saved')
      onClose()
    } catch (e) { err(e) }
  }
  const filters = r.filters || {}
  const setFilter = (k, v) => setR({ ...r, filters: { ...filters, [k]: v } })
  return (
    <Modal title={r.id ? `Edit rule — ${r.name}` : 'New listing rule'} onClose={onClose} wide>
      <div className="row">
        <Field label="Marketplace"><select value={r.marketplace} disabled={!!r.id}
          onChange={(e) => setR({ ...r, marketplace: e.target.value })}>
          {meta.marketplaces.map((m) => <option key={m}>{m}</option>)}</select></Field>
        <Field label="Name"><input value={r.name} onChange={(e) => setR({ ...r, name: e.target.value })} /></Field>
        <Field label="Priority (lower = first)"><input type="number" style={{ width: 60 }} value={r.priority}
          onChange={(e) => setR({ ...r, priority: Number(e.target.value) })} /></Field>
        <label style={{ alignSelf: 'center' }}><input type="checkbox" checked={r.active}
          onChange={(e) => setR({ ...r, active: e.target.checked })} /> active</label>
      </div>
      <h3>Filters</h3>
      <div className="row">
        <Field label="Games (csv)"><input value={(filters.games || []).join(',')}
          onChange={(e) => setFilter('games', e.target.value ? e.target.value.split(',').map((s) => s.trim()) : undefined)} /></Field>
        <Field label="Sets (csv)"><input value={(filters.sets || []).join(',')}
          onChange={(e) => setFilter('sets', e.target.value ? e.target.value.split(',').map((s) => s.trim().toUpperCase()) : undefined)} /></Field>
        <Field label="Price min"><input style={{ width: 70 }} value={filters.price_min ?? ''}
          onChange={(e) => setFilter('price_min', e.target.value === '' ? undefined : Number(e.target.value))} /></Field>
        <Field label="Price max"><input style={{ width: 70 }} value={filters.price_max ?? ''}
          onChange={(e) => setFilter('price_max', e.target.value === '' ? undefined : Number(e.target.value))} /></Field>
      </div>
      <div className="row center">
        <span className="muted">Condition allow-list:</span>
        {meta.conditions.map((c) => (
          <label key={c}><input type="checkbox"
            checked={(r.condition_allowlist || []).includes(c)}
            onChange={(e) => setR({
              ...r,
              condition_allowlist: e.target.checked
                ? [...(r.condition_allowlist || []), c]
                : (r.condition_allowlist || []).filter((x) => x !== c),
            })} /> {c}</label>))}
        <span className="muted">(none checked = all allowed)</span>
      </div>
      <div className="row center">
        <label><input type="checkbox" checked={r.block_sealed || false}
          onChange={(e) => setR({ ...r, block_sealed: e.target.checked })} /> block sealed</label>
        <label><input type="checkbox" checked={r.block_singles || false}
          onChange={(e) => setR({ ...r, block_singles: e.target.checked })} /> block singles</label>
      </div>
      {r.marketplace === 'ebay' && (<>
        <h3>eBay business policies <span className="muted">(IDs from Seller Hub)</span></h3>
        <div className="row">
          <Field label="Fulfillment policy ID"><input value={r.ebay_fulfillment_policy_id || ''}
            onChange={(e) => setR({ ...r, ebay_fulfillment_policy_id: e.target.value })} /></Field>
          <Field label="Payment policy ID"><input value={r.ebay_payment_policy_id || ''}
            onChange={(e) => setR({ ...r, ebay_payment_policy_id: e.target.value })} /></Field>
          <Field label="Return policy ID"><input value={r.ebay_return_policy_id || ''}
            onChange={(e) => setR({ ...r, ebay_return_policy_id: e.target.value })} /></Field>
          <Field label="Category ID"><input style={{ width: 90 }} value={r.ebay_category_id || ''}
            placeholder="183454" onChange={(e) => setR({ ...r, ebay_category_id: e.target.value })} /></Field>
        </div>
        <h3>Best Offer</h3>
        <div className="row center">
          <label><input type="checkbox" checked={r.best_offer?.enabled || false}
            onChange={(e) => setR({ ...r, best_offer: { ...r.best_offer, enabled: e.target.checked } })} /> enabled</label>
          <Field label="Auto-accept % of price"><input style={{ width: 60 }} value={r.best_offer?.auto_accept_pct ?? 90}
            onChange={(e) => setR({ ...r, best_offer: { ...r.best_offer, auto_accept_pct: Number(e.target.value) } })} /></Field>
          <Field label="Auto-decline % of price"><input style={{ width: 60 }} value={r.best_offer?.auto_decline_pct ?? 60}
            onChange={(e) => setR({ ...r, best_offer: { ...r.best_offer, auto_decline_pct: Number(e.target.value) } })} /></Field>
          <span className="muted">auto-accept is floored at FIFO cost</span>
        </div>
      </>)}
      <div className="row center"><button className="primary" onClick={save}>Save rule</button><Msg msg={msg} /></div>
    </Modal>
  )
}

function CredsModal({ account, onClose }) {
  const [msg, ok, err] = useMsg()
  const mk = account.marketplace
  const [creds, setCreds] = useState(
    mk === 'ebay'
      ? { client_id: '', client_secret: '', refresh_token: '', dry_run: true }
      : { api_enabled: false, dry_run: true })
  const save = async () => {
    try {
      await api.put(`/api/marketplaces/accounts/${mk}`, { credentials: creds })
      ok('Credentials saved (existing values replaced)')
    } catch (e) { err(e) }
  }
  return (
    <Modal title={`${mk} credentials`} onClose={onClose}>
      {mk === 'ebay' ? (<>
        <p className="muted">From your eBay developer keyset (production). Dry-run mode exercises the full flow without calling eBay.</p>
        <div className="row"><Field label="Client ID"><input style={{ width: 340 }} value={creds.client_id}
          onChange={(e) => setCreds({ ...creds, client_id: e.target.value })} /></Field></div>
        <div className="row"><Field label="Client Secret"><input type="password" style={{ width: 340 }} value={creds.client_secret}
          onChange={(e) => setCreds({ ...creds, client_secret: e.target.value })} /></Field></div>
        <div className="row"><Field label="Refresh Token"><input type="password" style={{ width: 340 }} value={creds.refresh_token}
          onChange={(e) => setCreds({ ...creds, refresh_token: e.target.value })} /></Field></div>
      </>) : (
        <p className="muted">TCGplayer API access is pending — enable once your rep issues keys. Until then use the CSV fallback exports.</p>
      )}
      <div className="row center">
        {mk === 'tcgplayer' && <label><input type="checkbox" checked={creds.api_enabled}
          onChange={(e) => setCreds({ ...creds, api_enabled: e.target.checked })} /> API enabled</label>}
        <label><input type="checkbox" checked={creds.dry_run}
          onChange={(e) => setCreds({ ...creds, dry_run: e.target.checked })} /> dry run</label>
        <button className="primary" onClick={save}>Save</button>
        <Msg msg={msg} />
      </div>
    </Modal>
  )
}

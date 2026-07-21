import React, { useEffect, useState } from 'react'
import { api, fmtMoney } from '../api.js'
import { CardSearch, Field, Modal, Msg, useMeta, useMsg } from '../components.jsx'

export default function CatalogPage() {
  const meta = useMeta()
  const [msg, ok, err] = useMsg()
  const [stats, setStats] = useState(null)
  const [sets, setSets] = useState([])
  const [game, setGame] = useState('mtg')
  const [setCode, setSetCode] = useState('')
  const [busy, setBusy] = useState('')
  const [detail, setDetail] = useState(null)
  const [browseSet, setBrowseSet] = useState('')
  const [browseCards, setBrowseCards] = useState([])
  const [browseBusy, setBrowseBusy] = useState(false)
  const [phashCov, setPhashCov] = useState(null)

  const refresh = () => api.get('/api/catalog/stats').then(setStats)
  const loadCoverage = () =>
    api.get(`/api/catalog/phash/coverage?game=${game}`).then(setPhashCov).catch(() => {})
  useEffect(() => { refresh() }, [])
  useEffect(() => {
    api.get(`/api/catalog/sets?game=${game}`).then(setSets)
    setBrowseSet(''); setBrowseCards([])
    loadCoverage()
  }, [game, stats])
  // While a background whole-catalog build runs, poll coverage so the bar climbs.
  useEffect(() => {
    if (!phashCov?.build?.running) return
    const t = setInterval(loadCoverage, 3000)
    return () => clearInterval(t)
  }, [phashCov?.build?.running, game])

  const buildAllPhashes = async () => {
    try {
      await api.post('/api/catalog/phash/build-all', { game })
      ok('Phash build started — runs in the background, skips what you already have')
      loadCoverage()
    } catch (e) { err(e) }
  }
  const buildSetPhashes = (code) =>
    run(`build phashes ${code}`, () =>
      api.post('/api/catalog/phash/build', { game, set_code: code })).then(loadCoverage)

  const loadSet = async (reset) => {
    if (!browseSet) return
    setBrowseBusy(true)
    try {
      const off = reset ? 0 : browseCards.length
      const p = new URLSearchParams({ game, set_code: browseSet, limit: '120', offset: String(off) })
      const rows = await api.get(`/api/catalog/search?${p}`)
      setBrowseCards(reset ? rows : [...browseCards, ...rows])
    } catch (e) { err(e) } finally { setBrowseBusy(false) }
  }

  const run = async (label, fn) => {
    setBusy(label)
    try {
      const r = await fn()
      ok(`${label}: ${JSON.stringify(r)}`)
      refresh()
    } catch (e) { err(e) } finally { setBusy('') }
  }

  const openDetail = async (c) => {
    try { setDetail(await api.get(`/api/catalog/card/${c.id}`)) } catch (e) { err(e) }
  }

  const perSet = game === 'mtg' || game === 'pokemon'

  if (!meta) return null
  return (
    <div>
      <h2>Catalog</h2>
      <div className="panel">
        <div className="row center">
          <Field label="Game"><select value={game} onChange={(e) => setGame(e.target.value)}>
            {meta.games.map((g) => <option key={g}>{g}</option>)}</select></Field>
          <button className="primary" disabled={!!busy} onClick={() => run('sync entire catalog',
            () => api.post('/api/catalog/sync/all', { game }))}>
            Sync entire catalog</button>
          <button disabled={!!busy} onClick={() => run('sync prices',
            () => api.post('/api/catalog/sync/prices', { game }))}>Sync prices (TCGcsv)</button>
          {game === 'mtg' && <button disabled={!!busy} onClick={() => run('backfill TCGplayer IDs',
            () => api.post('/api/catalog/backfill-ids', { game }))}
            title="Fill missing TCGplayer product ids on MTG tokens/promos from TCGcsv, then re-run Sync prices">
            Backfill TCGplayer IDs</button>}
          <button disabled={!!busy || phashCov?.build?.running} onClick={buildAllPhashes}
            title="Build every missing reference phash for this game in the background. Skips cards already hashed, so it's safe to re-run; watch progress in the coverage panel below.">
            {phashCov?.build?.running ? 'Building phashes…' : 'Build all phashes (background)'}</button>
          {game === 'pokemon' && (
            <button disabled={!!busy} title="Merge leftover pokemontcg.io cards into the current TCGcsv set codes and remove the duplicate sets"
              onClick={() => {
                if (!window.confirm('Clean up duplicate Pokémon sets?\n\nThis merges leftover legacy set codes (e.g. base4) into the current ones (e.g. BS2), moving any inventory/staging references across, then removes the emptied duplicate sets. Inventory rows are only re-pointed — nothing is deleted, and it is safe to re-run.'))
                  return
                run('dedupe Pokémon sets', () => api.post('/api/catalog/dedupe-pokemon', {}))
              }}>Clean up duplicate sets</button>)}
        </div>
        {busy && <p className="muted">Working: {busy}… (a full catalog sync can take several minutes — leave this tab open)</p>}
        <Msg msg={msg} />
        <p className="muted">
          <b>Sync entire catalog</b> pulls every set and card for the selected game in one pass —
          MTG via Scryfall's bulk-data feed, Pokémon and One Piece via TCGcsv, Yu-Gi-Oh! in a
          single call. Prices come from TCGcsv for all games. Reference phashes power the
          image-match fallback when OCR fails — build them per set (choose one below) or for the
          whole game as you scan new product.
        </p>
      </div>

      {perSet && (
        <details className="panel">
          <summary>Advanced: sync a single set</summary>
          <div className="row center" style={{ marginTop: 12 }}>
            <Field label="Set"><select value={setCode} onChange={(e) => setSetCode(e.target.value)}>
              <option value="">choose…</option>
              {sets.map((s) => <option key={s.code} value={s.code}>{s.code} — {s.name}</option>)}</select></Field>
            <button disabled={!!busy || !setCode} onClick={() => run(`sync cards ${setCode}`,
              () => api.post('/api/catalog/sync/cards', { game, set_code: setCode }))}>Sync this set's cards</button>
          </div>
          <p className="muted">Handy for topping up one freshly-released set without re-pulling the whole catalog.</p>
        </details>
      )}

      {stats && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Coverage</h3>
          <table><thead><tr><th>Game</th><th>Cards</th><th>Sets</th><th>Phashes</th></tr></thead>
            <tbody>{meta.games.map((g) => (
              <tr key={g}><td>{g}</td><td>{stats[g]?.cards}</td>
                <td>{stats[g]?.sets}</td><td>{stats[g]?.phashes}</td></tr>))}</tbody></table>
          <p className="muted">Price rows: {stats.price_rows}</p>
        </div>
      )}

      {phashCov && (
        <details className="panel">
          <summary>
            Reference phash coverage — <b>{phashCov.hashed}/{phashCov.total}</b> ({phashCov.pct}%)
            {phashCov.build?.running &&
              <span style={{ color: 'var(--blue)' }}> · building… {phashCov.build.built}/{phashCov.build.total}</span>}
            {phashCov.build?.error &&
              <span style={{ color: 'var(--red)' }}> · error: {phashCov.build.error}</span>}
          </summary>
          <p className="muted" style={{ marginTop: 12 }}>
            Per-set image-match coverage (least-covered first). "Build all" above does the whole game
            in the background; or fill one set at a time here. Already-hashed cards are always skipped.
          </p>
          <div className="table-wrap" style={{ maxHeight: 420, overflowY: 'auto' }}>
            <table><thead><tr><th>Set</th><th>Cards</th><th>Hashed</th><th>%</th><th></th></tr></thead>
              <tbody>{phashCov.sets.map((s) => (
                <tr key={s.set_code}>
                  <td>{s.set_code} <span className="muted">{s.set_name}</span></td>
                  <td>{s.cards}</td><td>{s.hashed}</td>
                  <td style={{ color: s.pct === 100 ? 'var(--green)' : s.pct === 0 ? 'var(--muted)' : 'inherit' }}>{s.pct}%</td>
                  <td>{s.hashed < s.cards &&
                    <button className="small" disabled={!!busy || phashCov.build?.running}
                      onClick={() => buildSetPhashes(s.set_code)}>Build</button>}</td>
                </tr>))}</tbody></table>
          </div>
        </details>
      )}

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Search catalog <span className="muted">(name, and/or set code + number)</span></h3>
        <CardSearch onSelect={openDetail} game={game} clearOnSelect={false} selectLabel="Details" />
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Browse a set</h3>
        <div className="row center">
          <Field label="Set"><select value={browseSet}
            onChange={(e) => { setBrowseSet(e.target.value); setBrowseCards([]) }}>
            <option value="">choose…</option>
            {sets.map((s) => <option key={s.code} value={s.code}>{s.code} — {s.name}</option>)}
          </select></Field>
          <button disabled={!browseSet || browseBusy} onClick={() => loadSet(true)}>List cards</button>
          {browseCards.length > 0 && <span className="muted">{browseCards.length} card(s) shown</span>}
        </div>
        {browseCards.length > 0 && (
          <div className="table-wrap" style={{ maxHeight: 520, overflowY: 'auto' }}>
            <table><thead><tr><th></th><th>#</th><th>Name</th><th>Rarity</th><th></th></tr></thead>
              <tbody>{browseCards.map((cd) => (
                <tr key={cd.id}>
                  <td>{cd.image_url && <img className="card-img" src={cd.image_url} alt="" />}</td>
                  <td>{cd.collector_number}</td>
                  <td>{cd.name}</td>
                  <td className="muted">{cd.rarity || '—'}</td>
                  <td><button className="small" onClick={() => openDetail(cd)}>Details</button></td>
                </tr>))}</tbody></table>
          </div>)}
        {browseCards.length > 0 && browseCards.length % 120 === 0 &&
          <button style={{ marginTop: 8 }} disabled={browseBusy} onClick={() => loadSet(false)}>Load more</button>}
      </div>

      {detail && (
        <Modal title={detail.card?.name || 'Card'} onClose={() => setDetail(null)}>
          <CardDetail detail={detail} />
        </Modal>
      )}
    </div>
  )
}

function CardDetail({ detail }) {
  const c = detail.card || {}
  const prices = detail.prices || []
  return (
    <div className="row" style={{ gap: 20, alignItems: 'flex-start' }}>
      {c.image_url && <img src={c.image_url} alt="" style={{ width: 240, borderRadius: 8 }} />}
      <div>
        <table><tbody>
          <tr><th style={{ textAlign: 'right' }}>Game</th><td>{c.game}</td></tr>
          <tr><th style={{ textAlign: 'right' }}>Set</th><td>{c.set_code} — {c.set_name}</td></tr>
          <tr><th style={{ textAlign: 'right' }}>Collector #</th><td>{c.collector_number}</td></tr>
          <tr><th style={{ textAlign: 'right' }}>Rarity</th><td>{c.rarity || '—'}</td></tr>
          <tr><th style={{ textAlign: 'right' }}>Finishes</th><td>{(c.finishes || []).join(', ') || '—'}</td></tr>
          <tr><th style={{ textAlign: 'right' }}>TCGplayer ID</th><td>{c.tcgplayer_product_id || '—'}</td></tr>
        </tbody></table>

        <h4 style={{ marginBottom: 4 }}>Prices (TCGcsv)</h4>
        {prices.length === 0 ? (
          <p className="muted">No price data — run “Sync prices” for this game.</p>
        ) : (
          <table>
            <thead><tr><th>Type</th><th>Market</th><th>Mid</th><th>Low</th><th>Direct low</th></tr></thead>
            <tbody>{prices.map((p) => (
              <tr key={p.sub_type}>
                <td>{p.sub_type}</td><td>{fmtMoney(p.market)}</td><td>{fmtMoney(p.mid)}</td>
                <td>{fmtMoney(p.low)}</td><td>{fmtMoney(p.direct_low)}</td>
              </tr>))}</tbody>
          </table>
        )}
      </div>
    </div>
  )
}

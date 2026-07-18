import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtMoney } from '../api.js'

export default function Dashboard() {
  const [aging, setAging] = useState(null)
  const [stats, setStats] = useState(null)
  const [orders, setOrders] = useState([])
  const [staging, setStaging] = useState([])
  const [errors, setErrors] = useState([])

  useEffect(() => {
    api.get('/api/reports/aging').then(setAging).catch(() => {})
    api.get('/api/catalog/stats').then(setStats).catch(() => {})
    api.get('/api/orders?status=open').then(setOrders).catch(() => {})
    api.get('/api/staging').then(setStaging).catch(() => {})
    api.get('/api/marketplaces/errors').then(setErrors).catch(() => {})
  }, [])

  const totalUnits = aging
    ? Object.values(aging.buckets).reduce((s, b) => s + b.units, 0) + aging.unknown_age.units
    : 0

  return (
    <div>
      <h2>Dashboard</h2>
      <div className="stats">
        <div className="stat"><div className="value">{totalUnits}</div><div className="label">Units in inventory</div></div>
        <div className="stat"><div className="value">{fmtMoney(aging?.total_at_market)}</div><div className="label">Value at market</div></div>
        <div className="stat"><div className="value">{fmtMoney(aging?.total_at_cost)}</div><div className="label">Value at cost</div></div>
        <div className="stat"><div className="value">{orders.length}</div><div className="label">Open orders</div></div>
        <div className="stat"><div className="value">{staging.length}</div><div className="label">Staged rows</div></div>
        <div className="stat"><div className="value" style={errors.length ? { color: 'var(--red)' } : {}}>{errors.length}</div><div className="label">Listing errors</div></div>
      </div>

      {errors.length > 0 && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Listing errors</h3>
          <p className="muted">Items with sync errors are excluded from bulk pushes until resolved — <Link to="/marketplaces" style={{ color: 'var(--accent)' }}>resolve in Marketplaces</Link>.</p>
        </div>
      )}

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Catalog coverage</h3>
        {stats ? (
          <table>
            <thead><tr><th>Game</th><th>Cards</th><th>Sets</th><th>Reference phashes</th></tr></thead>
            <tbody>
              {['mtg', 'pokemon', 'onepiece', 'yugioh'].map((g) => (
                <tr key={g}>
                  <td>{g}</td><td>{stats[g]?.cards ?? 0}</td>
                  <td>{stats[g]?.sets ?? 0}</td><td>{stats[g]?.phashes ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <span className="muted">loading…</span>}
        <p className="muted">Price rows loaded: {stats?.price_rows ?? '…'} — sync catalogs and prices from the <Link to="/catalog" style={{ color: 'var(--accent)' }}>Catalog page</Link>.</p>
      </div>
    </div>
  )
}

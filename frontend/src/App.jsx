import React from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard.jsx'
import ScanPage from './pages/ScanPage.jsx'
import StagingPage from './pages/StagingPage.jsx'
import InventoryPage from './pages/InventoryPage.jsx'
import ImportPage from './pages/ImportPage.jsx'
import ExportPage from './pages/ExportPage.jsx'
import PricingPage from './pages/PricingPage.jsx'
import MarketplacesPage from './pages/MarketplacesPage.jsx'
import LotsPage from './pages/LotsPage.jsx'
import OrdersPage from './pages/OrdersPage.jsx'
import OrderIntakePage from './pages/OrderIntakePage.jsx'
import ReportsPage from './pages/ReportsPage.jsx'
import CustomItemsPage from './pages/CustomItemsPage.jsx'
import BulkPage from './pages/BulkPage.jsx'
import CatalogPage from './pages/CatalogPage.jsx'
import SettingsPage from './pages/SettingsPage.jsx'
import CycleCountPage from './pages/CycleCountPage.jsx'
import ExpensesPage from './pages/ExpensesPage.jsx'
import PurchasesPage from './pages/PurchasesPage.jsx'
import HelpPage from './pages/HelpPage.jsx'

const nav = [
  { section: 'Intake' },
  { to: '/scan', label: 'Scan' },
  { to: '/staging', label: 'Staging' },
  { to: '/import', label: 'CSV Import' },
  { section: 'Inventory' },
  { to: '/inventory', label: 'Inventory' },
  { to: '/cycle-counts', label: 'Cycle Counts' },
  { to: '/export', label: 'Export' },
  { to: '/custom', label: 'Custom Items' },
  { to: '/bulk', label: 'Bulk' },
  { section: 'Selling' },
  { to: '/pricing', label: 'Pricing' },
  { to: '/marketplaces', label: 'Marketplaces' },
  { to: '/lots', label: 'Lots' },
  { to: '/order-intake', label: 'Order Intake' },
  { to: '/orders', label: 'Orders' },
  { section: 'Data' },
  { to: '/reports', label: 'Reports' },
  { to: '/purchases', label: 'Purchases' },
  { to: '/expenses', label: 'Expenses' },
  { to: '/catalog', label: 'Catalog' },
  { to: '/settings', label: 'Settings' },
  { to: '/help', label: 'Help & Docs' },
]

export default function App() {
  return (
    <>
      <nav className="sidebar">
        <h1>cdh-sortswift</h1>
        <NavLink to="/" end>Dashboard</NavLink>
        {nav.map((n, i) =>
          n.section
            ? <div key={i} className="section">{n.section}</div>
            : <NavLink key={n.to} to={n.to}>{n.label}</NavLink>
        )}
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/scan" element={<ScanPage />} />
          <Route path="/staging" element={<StagingPage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/inventory" element={<InventoryPage />} />
          <Route path="/cycle-counts" element={<CycleCountPage />} />
          <Route path="/export" element={<ExportPage />} />
          <Route path="/custom" element={<CustomItemsPage />} />
          <Route path="/bulk" element={<BulkPage />} />
          <Route path="/pricing" element={<PricingPage />} />
          <Route path="/marketplaces" element={<MarketplacesPage />} />
          <Route path="/lots" element={<LotsPage />} />
          <Route path="/order-intake" element={<OrderIntakePage />} />
          <Route path="/orders" element={<OrdersPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/purchases" element={<PurchasesPage />} />
          <Route path="/expenses" element={<ExpensesPage />} />
          <Route path="/catalog" element={<CatalogPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/help" element={<HelpPage />} />
        </Routes>
      </main>
    </>
  )
}

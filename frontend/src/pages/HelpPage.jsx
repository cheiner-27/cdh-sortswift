import React, { useEffect, useState } from 'react'

// The docs live as plain markdown under /docs (served by Vite in dev and from
// the built dist in production) so they're also readable as files. This page
// renders them in-app with a small markdown subset renderer.
const DOCS = [
  ['overview', 'Overview'],
  ['scanning', 'Scanning'],
  ['pricing', 'Pricing rules'],
  ['catalog-sync', 'Catalog & price sync'],
  ['exports-imports', 'Exports & imports'],
  ['lots', 'Bulk lots'],
  ['orders', 'Orders & refunds'],
  ['expenses', 'Expenses'],
  ['cycle-counts', 'Cycle counts'],
  ['custom-items', 'Custom items'],
  ['bulk', 'Bulk'],
  ['glossary', 'Glossary'],
]

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
function inline(s) {
  s = esc(s)
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>')
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
  s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>')
  return s
}
function renderMarkdown(md) {
  const lines = md.replace(/\r\n/g, '\n').split('\n')
  let html = '', i = 0, list = null
  const closeList = () => { if (list) { html += `</${list}>`; list = null } }
  const special = /^(#{1,6}\s|>|\s*[-*]\s|\s*\d+\.\s|```)/
  while (i < lines.length) {
    const line = lines[i]
    if (line.trim().startsWith('```')) {
      closeList(); i++
      let code = ''
      while (i < lines.length && !lines[i].trim().startsWith('```')) { code += lines[i] + '\n'; i++ }
      i++
      html += `<pre class="doc-code"><code>${esc(code)}</code></pre>`; continue
    }
    let m
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      closeList(); const lv = Math.min(m[1].length + 1, 6)
      html += `<h${lv}>${inline(m[2])}</h${lv}>`; i++; continue
    }
    if (line.startsWith('>')) {
      closeList(); html += `<blockquote>${inline(line.replace(/^>\s?/, ''))}</blockquote>`; i++; continue
    }
    if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      if (list !== 'ul') { closeList(); html += '<ul>'; list = 'ul' }
      html += `<li>${inline(m[1])}</li>`; i++; continue
    }
    if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
      if (list !== 'ol') { closeList(); html += '<ol>'; list = 'ol' }
      html += `<li>${inline(m[1])}</li>`; i++; continue
    }
    if (line.trim() === '') { closeList(); i++; continue }
    closeList()
    let para = line; i++
    while (i < lines.length && lines[i].trim() !== '' && !special.test(lines[i])) { para += ' ' + lines[i]; i++ }
    html += `<p>${inline(para)}</p>`
  }
  closeList()
  return html
}

export default function HelpPage() {
  const [slug, setSlug] = useState(DOCS[0][0])
  const [html, setHtml] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    setError('')
    fetch(`/docs/${slug}.md`)
      .then((r) => { if (!r.ok) throw new Error(`could not load /docs/${slug}.md`); return r.text() })
      .then((md) => setHtml(renderMarkdown(md)))
      .catch((e) => { setHtml(''); setError(String(e.message || e)) })
  }, [slug])

  return (
    <div>
      <h2>Help &amp; Docs</h2>
      <div className="row" style={{ alignItems: 'flex-start', gap: 20 }}>
        <div className="panel" style={{ minWidth: 190, alignSelf: 'flex-start' }}>
          {DOCS.map(([s, label]) => (
            <div key={s}>
              <button className={`small ${s === slug ? 'primary' : ''}`} style={{ width: '100%', textAlign: 'left', marginBottom: 4 }}
                onClick={() => setSlug(s)}>{label}</button>
            </div>))}
        </div>
        <div className="panel doc-body" style={{ flex: 1 }}>
          {error
            ? <p className="error-text">{error} — rebuild the frontend so /docs is published (run.ps1), or open the markdown files under <code>frontend/public/docs/</code>.</p>
            : <div dangerouslySetInnerHTML={{ __html: html }} />}
        </div>
      </div>
    </div>
  )
}

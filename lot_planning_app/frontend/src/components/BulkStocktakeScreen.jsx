import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { getFinalValues, applyStocktakeBatch } from '../api'

const s = {
  wrap:     { flex: 1, overflow: 'auto', background: '#fff', padding: 16 },
  toolbar:  { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, fontSize: 12, flexWrap: 'wrap' },
  input:    { padding: '5px 8px', border: '1px solid #ccc', borderRadius: 5, fontSize: 12 },
  labelInput: { padding: '5px 8px', border: '1px solid #ccc', borderRadius: 5, fontSize: 12, width: 180 },
  progress: { fontSize: 12, color: '#666' },
  bar:      { width: 120, height: 6, background: '#eee', borderRadius: 3, overflow: 'hidden', display: 'inline-block', verticalAlign: 'middle', marginLeft: 6 },
  barFill:  (pct) => ({ width: `${pct}%`, height: '100%', background: '#1565C0' }),
  btn:      { padding: '7px 16px', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 12, fontWeight: 600 },
  table:    { width: '100%', borderCollapse: 'collapse', fontSize: 12 },
  th:       { textAlign: 'right', fontWeight: 600, color: '#666', background: '#fafafa', padding: '6px 10px', borderBottom: '1px solid #ddd', whiteSpace: 'nowrap', position: 'sticky', top: 0 },
  td:       { textAlign: 'right', padding: '4px 10px', borderBottom: '1px solid #eee' },
  numInput: { width: 90, padding: '4px 6px', border: '1px solid #ccc', borderRadius: 4, fontSize: 12, textAlign: 'right' },
  warnRow:  { background: '#fff6e0' },
  doneTag:  { fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 9, background: '#e3f6f3', color: '#0f8a7a' },
}

const WARN_THRESHOLD = 0.3  // flag rows where |diff| exceeds 30% of system value

function defaultBatchLabel(dateStr) {
  const d = new Date(dateStr + 'T00:00:00')
  return `${d.getFullYear()}年${d.getMonth() + 1}月度棚卸し`
}

export default function BulkStocktakeScreen({ products, editorName, onApplied }) {
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [batchLabel, setBatchLabel] = useState(defaultBatchLabel(new Date().toISOString().slice(0, 10)))
  const [systemValues, setSystemValues] = useState({})
  const [loadingValues, setLoadingValues] = useState(true)
  const [actuals, setActuals] = useState({})   // product_id -> string
  const [appliedIds, setAppliedIds] = useState(new Set())
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')

  const loadSystemValues = useCallback(async (d) => {
    setLoadingValues(true)
    try {
      const vals = await getFinalValues(d)
      setSystemValues(vals)
    } finally {
      setLoadingValues(false)
    }
  }, [])

  useEffect(() => { loadSystemValues(date) }, [date, loadSystemValues])

  const onDateChange = (newDate) => {
    setDate(newDate)
    setBatchLabel(defaultBatchLabel(newDate))
    setAppliedIds(new Set())
  }

  const rows = useMemo(() => products.map(p => {
    const sys = systemValues[p.id] ?? null
    const actualStr = actuals[p.id] ?? ''
    const actual = actualStr === '' ? null : Number(actualStr)
    const diff = (sys !== null && actual !== null) ? actual - sys : null
    const warn = diff !== null && sys > 0 && Math.abs(diff) / sys > WARN_THRESHOLD
    return { product: p, sys, actual, actualStr, diff, warn }
  }), [products, systemValues, actuals])

  const enteredCount = rows.filter(r => r.actualStr !== '').length
  const pct = products.length ? Math.round((enteredCount / products.length) * 100) : 0
  const visibleRows = query.trim() ? rows.filter(r => r.product.name.includes(query.trim())) : rows

  const handleSubmit = async () => {
    const items = rows.filter(r => r.actualStr !== '').map(r => ({
      product_id: r.product.id, actual_quantity: r.actual,
    }))
    if (!items.length) return
    setSubmitting(true)
    setError('')
    try {
      await applyStocktakeBatch({ date, batch_label: batchLabel, set_by: editorName || null, items })
      setAppliedIds(prev => new Set([...prev, ...items.map(i => i.product_id)]))
      await loadSystemValues(date)
      onApplied?.()
    } catch (e) {
      setError(e.response?.data?.detail || '適用に失敗しました。')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={s.wrap}>
      <div style={s.toolbar}>
        <b>月次棚卸し（一括入力）</b>
        <input style={s.input} type="date" value={date} onChange={e => onDateChange(e.target.value)} />
        <input style={s.labelInput} value={batchLabel} onChange={e => setBatchLabel(e.target.value)} placeholder="バッチ名（例: 7月度棚卸し）" />
        <input style={s.input} value={query} onChange={e => setQuery(e.target.value)} placeholder="品目名で検索（1件だけ直したい時）" autoFocus />
        {query.trim() && <span style={{ color: '#888' }}>{visibleRows.length}件表示中</span>}
        <span style={s.progress}>
          {enteredCount} / {products.length} 件入力済み
          <span style={s.bar}><span style={s.barFill(pct)}></span></span>
        </span>
        {loadingValues && <span style={{ color: '#888' }}>読み込み中...</span>}
        <div style={{ flex: 1 }} />
        <button style={{ ...s.btn, background: '#1565C0', color: '#fff' }} disabled={submitting || !enteredCount} onClick={handleSubmit}>
          {submitting ? '適用中...' : `適用（${enteredCount}件）`}
        </button>
      </div>
      {error && <div style={{ color: '#c0392b', fontSize: 12, marginBottom: 8 }}>{error}</div>}
      <table style={s.table}>
        <thead>
          <tr>
            <th style={{ ...s.th, textAlign: 'left' }}>品目名</th>
            <th style={s.th}>システム計算値</th>
            <th style={s.th}>実地棚卸し数量</th>
            <th style={s.th}>差分</th>
            <th style={s.th}></th>
          </tr>
        </thead>
        <tbody>
          {visibleRows.map(r => (
            <tr key={r.product.id} style={r.warn ? s.warnRow : undefined}>
              <td style={{ ...s.td, textAlign: 'left' }}>{r.product.name}</td>
              <td style={s.td}>{r.sys !== null ? r.sys.toLocaleString() : '—'}</td>
              <td style={s.td}>
                <input
                  style={s.numInput}
                  type="number"
                  value={r.actualStr}
                  onChange={e => setActuals(prev => ({ ...prev, [r.product.id]: e.target.value }))}
                />
              </td>
              <td style={{ ...s.td, fontWeight: 700, color: r.diff == null ? '#ccc' : r.diff < 0 ? '#c0392b' : r.diff > 0 ? '#0f8a7a' : '#888' }}>
                {r.diff == null ? '—' : `${r.diff > 0 ? '+' : ''}${r.diff.toLocaleString()}`}
              </td>
              <td style={s.td}>
                {appliedIds.has(r.product.id) && <span style={s.doneTag}>✓ 反映済み</span>}
                {r.warn && !appliedIds.has(r.product.id) && <span style={{ color: '#b8860b', fontSize: 11 }}>⚠ 差異大</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

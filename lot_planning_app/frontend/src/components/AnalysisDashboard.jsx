import React, { useState, useEffect, useCallback } from 'react'
import { getAnalysis } from '../api'

const s = {
  wrap:    { flex: 1, overflow: 'auto', background: '#fff', padding: 16 },
  toolbar: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, fontSize: 12 },
  input:   { padding: '5px 8px', border: '1px solid #ccc', borderRadius: 5, fontSize: 12 },
  table:   { width: '100%', borderCollapse: 'collapse', fontSize: 12 },
  th:      { textAlign: 'right', fontWeight: 600, color: '#666', background: '#fafafa', padding: '6px 10px', borderBottom: '1px solid #ddd', whiteSpace: 'nowrap' },
  td:      { textAlign: 'right', padding: '6px 10px', borderBottom: '1px solid #eee' },
  tag:     (bg, color) => ({ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 9, background: bg, color }),
}

function categorize(row) {
  if (row.buffer > 0 && row.min_final < row.buffer) return { label: '維持', bg: '#e8f1fb', color: '#1565C0' }
  if (row.diff_from_buffer > row.buffer * 0.5 && row.stockout_count === 0) return { label: '削減候補', bg: '#fdeedd', color: '#c0630c' }
  if (row.stockout_count > 0) return { label: '要監視', bg: '#fff6cf', color: '#8a6d00' }
  return { label: '維持', bg: '#e8f1fb', color: '#1565C0' }
}

export default function AnalysisDashboard({ defaultRange }) {
  const [start, setStart] = useState(defaultRange?.start || '')
  const [end, setEnd]     = useState(defaultRange?.end || '')
  const [rows, setRows]   = useState([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    if (!start || !end) return
    setLoading(true)
    try {
      const data = await getAnalysis(start, end)
      setRows(data.sort((a, b) => b.diff_from_buffer - a.diff_from_buffer))
    } finally {
      setLoading(false)
    }
  }, [start, end])

  useEffect(() => { load() }, [load])

  return (
    <div style={s.wrap}>
      <div style={s.toolbar}>
        <b>在庫分析</b>
        <input style={s.input} type="date" value={start} onChange={e => setStart(e.target.value)} />
        〜
        <input style={s.input} type="date" value={end} onChange={e => setEnd(e.target.value)} />
        {loading && <span style={{ color: '#888' }}>読み込み中...</span>}
      </div>
      <table style={s.table}>
        <thead>
          <tr>
            <th style={{ ...s.th, textAlign: 'left' }}>品目名</th>
            <th style={s.th}>平均最終在庫</th>
            <th style={s.th}>最小</th>
            <th style={s.th}>最大</th>
            <th style={s.th}>現行バッファ</th>
            <th style={s.th}>差分</th>
            <th style={s.th}>欠品回数</th>
            <th style={s.th}>分類</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const cat = categorize(r)
            return (
              <tr key={r.product_id}>
                <td style={{ ...s.td, textAlign: 'left' }}>{r.name}</td>
                <td style={s.td}>{r.avg_final.toLocaleString()}</td>
                <td style={s.td}>{r.min_final.toLocaleString()}</td>
                <td style={s.td}>{r.max_final.toLocaleString()}</td>
                <td style={s.td}>{r.buffer.toLocaleString()}</td>
                <td style={{ ...s.td, color: r.diff_from_buffer > 0 ? '#0f8a7a' : '#c0392b', fontWeight: 700 }}>
                  {r.diff_from_buffer > 0 ? '+' : ''}{r.diff_from_buffer.toLocaleString()}
                </td>
                <td style={s.td}>{r.stockout_count}</td>
                <td style={s.td}><span style={s.tag(cat.bg, cat.color)}>{cat.label}</span></td>
              </tr>
            )
          })}
          {!loading && rows.length === 0 && (
            <tr><td colSpan={8} style={{ ...s.td, textAlign: 'center', color: '#888' }}>データがありません</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

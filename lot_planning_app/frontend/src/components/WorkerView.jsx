import React, { useMemo } from 'react'

const s = {
  wrap:      { flex: 1, overflow: 'auto', background: '#fff' },
  table:     { borderCollapse: 'collapse', fontSize: 16, minWidth: '100%' },
  nameCol:   { position: 'sticky', left: 0, zIndex: 2, background: '#fafafa', textAlign: 'left', padding: '10px 14px', borderRight: '2px solid #999', borderBottom: '1px solid #ddd', fontWeight: 700, minWidth: 220, maxWidth: 220 },
  dateHead:  (isToday) => ({ position: 'sticky', top: 0, zIndex: 1, background: isToday ? '#1565C0' : '#fafafa', color: isToday ? '#fff' : '#333', textAlign: 'center', padding: '8px 10px', borderBottom: '2px solid #999', borderLeft: '1px solid #ddd', fontWeight: 700, whiteSpace: 'nowrap' }),
  noteCell:  (isToday) => ({ textAlign: 'left', padding: '8px 10px', borderLeft: '1px solid #eee', borderBottom: '1px solid #f0f0f0', background: isToday ? '#FFF8E1' : '#FDF0DC', color: '#8a5a1e', fontStyle: 'italic', minWidth: 90 }),
  planCell:  (isToday) => ({ textAlign: 'center', padding: '8px 10px', borderLeft: '1px solid #eee', borderBottom: '2px solid #ddd', background: isToday ? '#E3F0FF' : '#E8F4FD', fontWeight: 700, minWidth: 90 }),
  productHead: { position: 'sticky', left: 0, zIndex: 2, background: '#fafafa', borderRight: '2px solid #999', borderBottom: '2px solid #999' },
  rowLabel:  { fontSize: 11, color: '#888', fontWeight: 400 },
}

function todayISO() { return new Date().toISOString().slice(0, 10) }

export default function WorkerView({ gridData }) {
  const { dates, row_types, products } = gridData
  const noteRt = row_types.find(rt => rt.role === 'note')
  const planRt = row_types.find(rt => rt.role === 'plan')
  const today = todayISO()

  const rows = useMemo(() => products.map(p => ({
    product: p,
    notes: noteRt ? (p.values[String(noteRt.id)] || {}) : {},
    plans: planRt ? (p.values[String(planRt.id)] || {}) : {},
  })), [products, noteRt, planRt])

  if (!noteRt || !planRt) {
    return <div style={{ padding: 24, color: '#888' }}>備考・計画の行種別が見つかりません。</div>
  }

  return (
    <div style={s.wrap}>
      <table style={s.table}>
        <thead>
          <tr>
            <th style={s.productHead}>品目名</th>
            {dates.map(d => (
              <th key={d} style={s.dateHead(d === today)}>
                {d.slice(5).replace('-', '/')}{d === today ? ' 今日' : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ product, notes, plans }) => (
            <React.Fragment key={product.id}>
              <tr>
                <td style={s.nameCol} rowSpan={2}>{product.name}</td>
                {dates.map(d => (
                  <td key={d} style={s.noteCell(d === today)}>{notes[d] || ''}</td>
                ))}
              </tr>
              <tr>
                {dates.map(d => (
                  <td key={d} style={s.planCell(d === today)}>{plans[d] ?? ''}</td>
                ))}
              </tr>
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}

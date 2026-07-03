import React, { useState, useRef } from 'react'

const s = {
  sidebar: { width: 220, background: '#fff', borderRight: '1px solid #ddd', display: 'flex', flexDirection: 'column', overflowY: 'auto', padding: 12 },
  section: { marginBottom: 16 },
  heading: { fontWeight: 700, fontSize: 11, textTransform: 'uppercase', color: '#666', marginBottom: 6 },
  label: { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4, cursor: 'pointer', fontSize: 12 },
  input: { width: '100%', padding: '4px 6px', border: '1px solid #ccc', borderRadius: 4, fontSize: 12, marginBottom: 6 },
  select: { width: '100%', padding: '4px 6px', border: '1px solid #ccc', borderRadius: 4, fontSize: 12, marginBottom: 6 },
  btn: { width: '100%', padding: '5px 0', border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 12 },
}

export default function Sidebar({ rowTypes, visibleRowTypes, onToggleRowType,
                                   filters, onFilterChange, onAddRowType, onReorder }) {
  const [newRowName, setNewRowName] = useState('')
  const [newRowRole, setNewRowRole] = useState('demand_actual')
  const dragId = useRef(null)
  const dragOverId = useRef(null)

  const handleDragStart = (e, id) => {
    dragId.current = id
    e.dataTransfer.effectAllowed = 'move'
  }
  const handleDragOver = (e, id) => {
    e.preventDefault()
    dragOverId.current = id
  }
  const handleDrop = () => {
    if (dragId.current === dragOverId.current) return
    const from = rowTypes.findIndex(r => r.id === dragId.current)
    const to   = rowTypes.findIndex(r => r.id === dragOverId.current)
    if (from === -1 || to === -1) return
    const next = [...rowTypes]
    const [moved] = next.splice(from, 1)
    next.splice(to, 0, moved)
    onReorder(next.map(r => r.id))
  }

  const handleAdd = () => {
    if (!newRowName.trim()) return
    onAddRowType({ name: newRowName.trim(), role: newRowRole })
    setNewRowName('')
  }

  const planners     = [...new Set((filters._allPlanners    || []))].filter(Boolean)
  const locations    = [...new Set((filters._allLocations   || []))].filter(Boolean)
  const categories   = [...new Set((filters._allCategories  || []))].filter(Boolean)

  return (
    <div style={s.sidebar}>
      {/* Row visibility */}
      <div style={s.section}>
        <div style={s.heading}>表示する行</div>
        {rowTypes.map(rt => (
          <div
            key={rt.id}
            draggable
            onDragStart={e => handleDragStart(e, rt.id)}
            onDragOver={e => handleDragOver(e, rt.id)}
            onDrop={handleDrop}
            style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 4, cursor: 'grab' }}
          >
            <span style={{ color: '#bbb', fontSize: 14, userSelect: 'none', flexShrink: 0 }}>⠿</span>
            <label style={{ ...s.label, margin: 0, cursor: 'pointer', flex: 1 }}>
              <input type="checkbox" checked={visibleRowTypes.includes(rt.id)}
                onChange={() => onToggleRowType(rt.id)} />
              {rt.name}
            </label>
          </div>
        ))}
      </div>

      {/* Add custom row */}
      <div style={s.section}>
        <div style={s.heading}>行を追加</div>
        <input style={s.input} placeholder="行名（例: 虎ノ門店）" value={newRowName}
          onChange={e => setNewRowName(e.target.value)} />
        <select style={s.select} value={newRowRole} onChange={e => setNewRowRole(e.target.value)}>
          <option value="demand_actual">出荷（実績）</option>
          <option value="demand_forecast">出荷予測</option>
          <option value="adjustment">調整</option>
        </select>
        <button style={{ ...s.btn, background: '#1976D2', color: '#fff' }} onClick={handleAdd}>
          追加
        </button>
      </div>

      {/* Product filters */}
      <div style={s.section}>
        <div style={s.heading}>商品フィルター</div>
        <input style={s.input} placeholder="品目名で検索" value={filters.q || ''}
          onChange={e => onFilterChange({ q: e.target.value })} />

        {planners.length > 0 && (
          <select style={s.select} value={filters.planner || ''}
            onChange={e => onFilterChange({ planner: e.target.value })}>
            <option value="">すべての担当者</option>
            {planners.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        )}

        {locations.length > 0 && (
          <select style={s.select} value={filters.mfg_location || ''}
            onChange={e => onFilterChange({ mfg_location: e.target.value })}>
            <option value="">すべての製造場所</option>
            {locations.map(l => <option key={l} value={l}>{l}</option>)}
          </select>
        )}

        {categories.length > 0 && (
          <select style={s.select} value={filters.category || ''}
            onChange={e => onFilterChange({ category: e.target.value })}>
            <option value="">すべての分類</option>
            {categories.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
      </div>
    </div>
  )
}

import React, { useState, useEffect } from 'react'
import axios from 'axios'
import { getProductAttributes, createAttribute, setAttributeValue } from '../api'

const s = {
  overlay:  { position: 'fixed', inset: 0, zIndex: 100, display: 'flex', justifyContent: 'flex-end' },
  backdrop: { position: 'absolute', inset: 0, background: 'rgba(0,0,0,.25)' },
  panel:    { position: 'relative', width: 340, background: '#fff', boxShadow: '-4px 0 20px rgba(0,0,0,.15)',
               display: 'flex', flexDirection: 'column', overflowY: 'auto', zIndex: 1 },
  header:   { padding: '16px 20px 12px', background: '#1565C0', color: '#fff', flexShrink: 0 },
  title:    { fontSize: 15, fontWeight: 700, marginBottom: 2 },
  code:     { fontSize: 12, opacity: 0.8 },
  body:     { padding: '16px 20px', flex: 1 },
  section:  { marginBottom: 20 },
  sHead:    { fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: '#888', marginBottom: 8,
               borderBottom: '1px solid #eee', paddingBottom: 4 },
  row:      { display: 'flex', alignItems: 'center', marginBottom: 10, gap: 8 },
  label:    { width: 110, fontSize: 12, color: '#555', flexShrink: 0 },
  input:    { flex: 1, padding: '5px 8px', border: '1px solid #ccc', borderRadius: 5, fontSize: 13 },
  formula:  { flex: 1, padding: '6px 8px', background: '#F5F5F5', border: '1px solid #ddd',
               borderRadius: 5, fontSize: 12, color: '#333', fontFamily: 'monospace' },
  footer:   { padding: '12px 20px', borderTop: '1px solid #eee', display: 'flex', gap: 8, flexShrink: 0 },
  btn:      { flex: 1, padding: '7px 0', border: 'none', borderRadius: 5, cursor: 'pointer', fontSize: 13, fontWeight: 600 },
}

export default function ProductPanel({ product, monthId, rowTypes = [], visibleRowTypes = [], productRowOverrides = {}, onSetProductRows, onClose, onSaved }) {
  const [form, setForm] = useState({})
  const [saving, setSaving] = useState(false)
  const [startingInv, setStartingInv] = useState('')
  const [localRowIds, setLocalRowIds] = useState([])
  const [planners, setPlanners] = useState([])
  const [plannerInput, setPlannerInput] = useState('')
  const [locations, setLocations] = useState([])
  const [locationInput, setLocationInput] = useState('')
  const [customAttrs, setCustomAttrs] = useState([])
  const [showAddAttr, setShowAddAttr] = useState(false)
  const [newAttrLabel, setNewAttrLabel] = useState('')
  const [newAttrType, setNewAttrType] = useState('text')

  useEffect(() => {
    if (!product) return
    const override = productRowOverrides[product.id]
    setLocalRowIds(override !== undefined ? override : visibleRowTypes)
    setPlanners(Array.isArray(product.planner) ? product.planner : (product.planner ? [product.planner] : []))
    setPlannerInput('')
    setLocations(Array.isArray(product.mfg_location) ? product.mfg_location : (product.mfg_location ? [product.mfg_location] : []))
    setLocationInput('')
    setForm({
      name:         product.name         ?? '',
      code:         product.code         ?? '',
      category:     product.category     ?? '',
      lot_size:     product.lot_size     ?? 1,
      lot_unit:     product.lot_unit     ?? 'p',
      lead_time:    product.lead_time    ?? 0,
      min_stock:    product.min_stock    ?? 0,
      max_stock:    product.max_stock    ?? 0,
      notes:        product.notes        ?? '',
    })
    setStartingInv(product.starting_inventory ?? 0)
    getProductAttributes(product.id).then(setCustomAttrs)
  }, [product])

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const handleAddAttr = async () => {
    if (!newAttrLabel.trim()) return
    const label = newAttrLabel.trim()
    const name  = label.toLowerCase().replace(/\s+/g, '_').replace(/[^\w]/g, '')
    await createAttribute({ name: name || `attr_${Date.now()}`, label, attr_type: newAttrType })
    const attrs = await getProductAttributes(product.id)
    setCustomAttrs(attrs)
    setNewAttrLabel('')
    setNewAttrType('text')
    setShowAddAttr(false)
  }

  const toggleRowId = (id) => {
    setLocalRowIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await axios.patch(`/api/products/${product.id}`, {
        ...form,
        planner:      planners,
        mfg_location: locations,
        lot_size:  Number(form.lot_size),
        lead_time: Number(form.lead_time),
        min_stock: Number(form.min_stock),
        max_stock: Number(form.max_stock),
      })
      await axios.patch(`/api/months/${monthId}/inventory/${product.id}`, {
        starting_inventory: Number(startingInv),
      })
      await Promise.all(
        customAttrs.map(a => setAttributeValue(product.id, a.id, a.value ?? ''))
      )
      onSetProductRows?.(product.id, localRowIds)
      onSaved()
    } finally {
      setSaving(false)
    }
  }

  if (!product) return null

  const leadTime  = Number(form.lead_time) || 0
  const lotSize   = Number(form.lot_size)  || 1
  const yoteiFormula = leadTime === 0
    ? `計画（倍）× ${lotSize} （当日入庫）`
    : `計画（倍）[${leadTime}日前] × ${lotSize}`

  return (
    <div style={s.overlay}>
      <div style={s.backdrop} onClick={onClose} />
      <div style={s.panel}>
        <div style={s.header}>
          <div style={s.title}>{product.name}</div>
          {product.code && <div style={s.code}>{product.code}</div>}
        </div>

        <div style={s.body}>

          {/* Per-product row visibility */}
          {rowTypes.length > 0 && (
            <div style={s.section}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, borderBottom: '1px solid #eee', paddingBottom: 4 }}>
                <span style={{ ...s.sHead, margin: 0 }}>表示する行</span>
                <button
                  onClick={() => setLocalRowIds(visibleRowTypes)}
                  style={{ fontSize: 11, color: '#1565C0', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
                  デフォルトに戻す
                </button>
              </div>
              {rowTypes.map(rt => (
                <div key={rt.id} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <input type="checkbox" id={`rt-${rt.id}`}
                    checked={localRowIds.includes(rt.id)}
                    onChange={() => toggleRowId(rt.id)} />
                  <label htmlFor={`rt-${rt.id}`} style={{ fontSize: 12, cursor: 'pointer' }}>{rt.name}</label>
                </div>
              ))}
            </div>
          )}

          {/* 在庫 */}
          <div style={s.section}>
            <div style={s.sHead}>在庫</div>
            <div style={s.row}>
              <span style={s.label}>月初在庫数</span>
              <input style={s.input} type="number" value={startingInv}
                onChange={e => setStartingInv(e.target.value)} />
            </div>
            <div style={s.row}>
              <span style={s.label}>最低在庫</span>
              <input style={s.input} type="number" value={form.min_stock}
                onChange={e => set('min_stock', e.target.value)} />
            </div>
            <div style={s.row}>
              <span style={s.label}>最高在庫</span>
              <input style={s.input} type="number" value={form.max_stock}
                onChange={e => set('max_stock', e.target.value)} />
            </div>
          </div>

          {/* 入庫予定数 formula */}
          <div style={s.section}>
            <div style={s.sHead}>入庫予定数の計算式</div>
            <div style={s.row}>
              <span style={s.label}>ロット数</span>
              <input style={s.input} type="number" value={form.lot_size}
                onChange={e => set('lot_size', e.target.value)} />
              <input style={{ ...s.input, width: 40, flex: 'none' }} value={form.lot_unit}
                onChange={e => set('lot_unit', e.target.value)} />
            </div>
            <div style={s.row}>
              <span style={s.label}>リードタイム</span>
              <input style={s.input} type="number" min="0" value={form.lead_time}
                onChange={e => set('lead_time', e.target.value)} />
              <span style={{ fontSize: 12, color: '#888' }}>日</span>
            </div>
            <div style={{ ...s.row, marginTop: 4 }}>
              <span style={s.label}>結果の式</span>
              <div style={s.formula}>{yoteiFormula}</div>
            </div>
          </div>

          {/* 基本情報 */}
          <div style={s.section}>
            <div style={s.sHead}>基本情報</div>
            {[
              ['品目名',    'name',         'text'],
              ['コード',    'code',         'text'],
              ['分類',      'category',     'text'],
              ['備考',      'notes',        'text'],
            ].map(([label, key, type]) => (
              <div key={key} style={s.row}>
                <span style={s.label}>{label}</span>
                <input style={s.input} type={type} value={form[key]}
                  onChange={e => set(key, e.target.value)} />
              </div>
            ))}

            {/* 製造場所 — multi-tag input */}
            <div style={{ marginBottom: 10 }}>
              <span style={{ ...s.label, display: 'block', marginBottom: 4 }}>製造場所</span>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 4 }}>
                {locations.map(name => (
                  <span key={name} style={{ display: 'inline-flex', alignItems: 'center', gap: 3,
                    background: '#E8F5E9', color: '#2E7D32', borderRadius: 12,
                    padding: '2px 8px', fontSize: 12 }}>
                    {name}
                    <button onClick={() => setLocations(prev => prev.filter(n => n !== name))}
                      style={{ background: 'none', border: 'none', cursor: 'pointer',
                        color: '#2E7D32', padding: 0, fontSize: 13, lineHeight: 1 }}>×</button>
                  </span>
                ))}
              </div>
              <input
                style={s.input}
                placeholder="製造場所を追加してEnter"
                value={locationInput}
                onChange={e => setLocationInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && locationInput.trim()) {
                    e.preventDefault()
                    const name = locationInput.trim()
                    if (!locations.includes(name)) setLocations(prev => [...prev, name])
                    setLocationInput('')
                  }
                }}
              />
            </div>

            {/* 担当者 — multi-tag input */}
            <div style={{ marginBottom: 10 }}>
              <span style={{ ...s.label, display: 'block', marginBottom: 4 }}>担当者</span>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 4 }}>
                {planners.map(name => (
                  <span key={name} style={{ display: 'inline-flex', alignItems: 'center', gap: 3,
                    background: '#E3F2FD', color: '#1565C0', borderRadius: 12,
                    padding: '2px 8px', fontSize: 12 }}>
                    {name}
                    <button onClick={() => setPlanners(prev => prev.filter(n => n !== name))}
                      style={{ background: 'none', border: 'none', cursor: 'pointer',
                        color: '#1565C0', padding: 0, fontSize: 13, lineHeight: 1 }}>×</button>
                  </span>
                ))}
              </div>
              <input
                style={s.input}
                placeholder="担当者を追加してEnter"
                value={plannerInput}
                onChange={e => setPlannerInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && plannerInput.trim()) {
                    e.preventDefault()
                    const name = plannerInput.trim()
                    if (!planners.includes(name)) setPlanners(prev => [...prev, name])
                    setPlannerInput('')
                  }
                }}
              />
            </div>

            {/* Custom attributes */}
            {customAttrs.map(a => (
              <div key={a.id} style={s.row}>
                <span style={s.label}>{a.label}</span>
                <input
                  style={s.input}
                  type={a.attr_type === 'number' ? 'number' : 'text'}
                  value={a.value ?? ''}
                  onChange={e => setCustomAttrs(prev =>
                    prev.map(x => x.id === a.id ? { ...x, value: e.target.value } : x)
                  )}
                />
              </div>
            ))}

            {/* Add new attribute */}
            {showAddAttr ? (
              <div style={{ background: '#F5F5F5', borderRadius: 6, padding: '10px 10px 6px', marginTop: 8 }}>
                <input
                  style={{ ...s.input, marginBottom: 6 }}
                  placeholder="フィールド名（例: 仕入先）"
                  value={newAttrLabel}
                  onChange={e => setNewAttrLabel(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleAddAttr()}
                  autoFocus
                />
                <div style={{ display: 'flex', gap: 6 }}>
                  <select
                    style={{ ...s.input, flex: 'none', width: 90 }}
                    value={newAttrType}
                    onChange={e => setNewAttrType(e.target.value)}
                  >
                    <option value="text">テキスト</option>
                    <option value="number">数値</option>
                  </select>
                  <button onClick={handleAddAttr}
                    style={{ ...s.btn, background: '#1565C0', color: '#fff', flex: 1 }}>追加</button>
                  <button onClick={() => { setShowAddAttr(false); setNewAttrLabel('') }}
                    style={{ ...s.btn, background: '#eee', color: '#333', flex: 'none', width: 60 }}>×</button>
                </div>
              </div>
            ) : (
              <button onClick={() => setShowAddAttr(true)}
                style={{ background: 'none', border: '1px dashed #ccc', borderRadius: 5, width: '100%',
                  padding: '5px 0', fontSize: 12, color: '#888', cursor: 'pointer', marginTop: 4 }}>
                ＋ フィールドを追加
              </button>
            )}
          </div>

        </div>

        <div style={s.footer}>
          <button style={{ ...s.btn, background: '#eee', color: '#333' }} onClick={onClose}>
            キャンセル
          </button>
          <button style={{ ...s.btn, background: '#1565C0', color: '#fff' }}
            disabled={saving} onClick={handleSave}>
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}

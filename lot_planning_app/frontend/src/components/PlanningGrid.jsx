import React, { useCallback, useMemo, useRef, useState, useEffect } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { updateValue, updateValuesBatch } from '../api'

const ROLE_COLORS = {
  note:             '#FDF0DC',
  plan:             '#E8F4FD',
  inbound_planned:  '#FFF9E6',
  inbound_actual:   '#E6F4EA',
  demand_forecast:  '#FFF3E0',
  demand_actual:    '#FFFFFF',
  adjustment:       '#F3E5F5',
  final:            '#E8EAF6',
}

const EDITABLE_ROLES = new Set(['plan', 'inbound_actual', 'demand_forecast', 'demand_actual', 'adjustment'])

const FLAG_COLORS = ['#e6821e', '#c0392b', '#2e7d32', '#1565C0', '#7b3fb0']
const LINK_TYPES = [
  { id: 'reschedule', label: '予定変更（同じ品目・別日）', color: '#e6821e' },
  { id: 'split',      label: '分割（1ロットが複数品目へ）', color: '#7b3fb0' },
  { id: 'transfer',   label: '移動（蔵替など、別の保管先へ）', color: '#0f8a7a' },
]
const LINK_COLOR = Object.fromEntries(LINK_TYPES.map(t => [t.id, t.color]))

function flagKey(productId, rowTypeId, date) { return `${productId}:${rowTypeId}:${date}` }

export default function PlanningGrid({
  gridData, visibleRowTypes, productRowOverrides = {}, cellFlags = {}, lotLinks = [], canceledCells = {},
  flagMode = false, linkMode = false, cancelMode = false,
  onCellUpdated, onProductClick, onSetFlag, onClearFlag, onCreateLink, onDeleteLink, onToggleCancel, onExtendRange,
}) {
  const gridRef = useRef()
  const containerRef = useRef()
  const { dates, row_types, products } = gridData

  const [popover, setPopover] = useState(null)       // {x,y,productId,rowTypeId,date,existing}
  const [pendingSource, setPendingSource] = useState(null)  // {productId,rowTypeId,date}
  const [linkModal, setLinkModal] = useState(null)    // {source,target}
  const [overlayTick, setOverlayTick] = useState(0)

  // Build flat row array with rowSpan metadata
  const rowData = useMemo(() => {
    if (!products || !row_types) return []
    const globalVisible = new Set(visibleRowTypes)
    const rows = []
    for (const product of products) {
      const override = productRowOverrides[product.id]
      const productVisible = override !== undefined ? new Set(override) : globalVisible
      const visibleRts = row_types.filter(rt => productVisible.has(rt.id))
      if (!visibleRts.length) continue
      for (let i = 0; i < visibleRts.length; i++) {
        const rt = visibleRts[i]
        const row = {
          _productId:       product.id,
          _productName:     product.name,
          _productCode:     product.code,
          _rowTypeId:       rt.id,
          _rowTypeName:     rt.name,
          _role:            rt.role,
          _minStock:        product.min_stock,
          _maxStock:        product.max_stock,
          _isFirstRow:      i === 0,
          _isLastRow:       i === visibleRts.length - 1,
          _productRowCount: visibleRts.length,
        }
        const vals = product.values[String(rt.id)] || {}
        for (const d of dates) row[d] = vals[d] ?? null
        rows.push(row)
      }
    }
    return rows
  }, [products, row_types, dates, visibleRowTypes, productRowOverrides])

  const rowIndexOf = useCallback((productId, rowTypeId) =>
    rowData.findIndex(r => r._productId === productId && r._rowTypeId === rowTypeId),
  [rowData])

  const openFlagPopover = useCallback((e, data, date) => {
    const rect = e.event.target.getBoundingClientRect()
    const containerRect = containerRef.current.getBoundingClientRect()
    setPopover({
      x: rect.left - containerRect.left,
      y: rect.bottom - containerRect.top,
      productId: data._productId,
      rowTypeId: data._rowTypeId,
      date,
      existing: cellFlags[flagKey(data._productId, data._rowTypeId, date)],
    })
  }, [cellFlags])

  const onDateCellClicked = useCallback((p) => {
    const date = p.colDef.field
    if (flagMode) {
      openFlagPopover(p, p.data, date)
      return
    }
    if (linkMode) {
      const here = { productId: p.data._productId, rowTypeId: p.data._rowTypeId, date }
      if (!pendingSource) {
        setPendingSource(here)
      } else if (pendingSource.productId === here.productId && pendingSource.rowTypeId === here.rowTypeId && pendingSource.date === here.date) {
        setPendingSource(null)  // clicked same cell again — cancel
      } else {
        setLinkModal({ source: pendingSource, target: here })
        setPendingSource(null)
      }
      return
    }
    if (cancelMode) {
      onToggleCancel?.(p.data._productId, p.data._rowTypeId, date)
    }
  }, [flagMode, linkMode, cancelMode, pendingSource, openFlagPopover, onToggleCancel])

  const columnDefs = useMemo(() => {
    if (!dates) return []

    const pinned = [
      {
        field: '_productName',
        headerName: '品目名',
        pinned: 'left',
        width: 190,
        rowSpan: (p) => p.data._isFirstRow ? p.data._productRowCount : 1,
        onCellClicked: (p) => {
          if (p.data._isFirstRow && onProductClick) {
            const product = products.find(pr => pr.id === p.data._productId)
            if (product) onProductClick(product)
          }
        },
        cellStyle: (p) => ({
          fontWeight: 600,
          fontSize: 12,
          borderRight: '1px solid #ccc',
          borderBottom: p.data._isLastRow ? '2px solid #9E9E9E' : 'none',
          background: '#FAFAFA',
          whiteSpace: 'normal',
          lineHeight: '1.3',
          display: 'flex',
          alignItems: 'center',
          cursor: p.data._isFirstRow ? 'pointer' : 'default',
          color: p.data._isFirstRow ? '#1565C0' : '#000',
          textDecoration: p.data._isFirstRow ? 'underline' : 'none',
        }),
      },
      {
        field: '_rowTypeName',
        headerName: '行種別',
        pinned: 'left',
        width: 100,
        cellStyle: (p) => ({
          fontSize: 11,
          color: '#555',
          background: ROLE_COLORS[p.data._role] || '#fff',
          borderBottom: p.data._isLastRow ? '2px solid #9E9E9E' : undefined,
        }),
      },
    ]

    const byMonth = {}
    for (const d of dates) {
      const obj = new Date(d + 'T00:00:00')
      const key = `${obj.getFullYear()}-${obj.getMonth()}`
      const label = `${obj.getFullYear()}/${obj.getMonth() + 1}`
      if (!byMonth[key]) byMonth[key] = { label, dates: [] }
      byMonth[key].dates.push({ iso: d, day: obj.getDate() })
    }

    const dateCols = Object.values(byMonth).map(group => ({
      headerName: group.label,
      children: group.dates.map(({ iso, day }) => ({
        field: iso,
        headerName: String(day),
        width: 46,
        editable: (p) => !flagMode && !linkMode && !cancelMode && EDITABLE_ROLES.has(p.data._role),
        onCellClicked: onDateCellClicked,
        tooltipValueGetter: (p) => {
          const flag = cellFlags[flagKey(p.data._productId, p.data._rowTypeId, iso)]
          const cancel = canceledCells[flagKey(p.data._productId, p.data._rowTypeId, iso)]
          const parts = []
          if (cancel) {
            const who = cancel.canceled_by ? `${cancel.canceled_by} が` : ''
            parts.push(`${who}取り消し ${cancel.canceled_at ? new Date(cancel.canceled_at).toLocaleString('ja-JP') : ''}${cancel.note ? ' — ' + cancel.note : ''}（計算から除外）`)
          }
          if (flag) {
            const who = flag.set_by ? `${flag.set_by} が` : ''
            parts.push(`${who}変更 ${flag.set_at ? new Date(flag.set_at).toLocaleString('ja-JP') : ''}${flag.note ? ' — ' + flag.note : ''}`)
          }
          return parts.length ? parts.join(' / ') : undefined
        },
        cellStyle: (p) => {
          const base = {
            textAlign: p.data._role === 'note' ? 'left' : 'right',
            fontSize: 12,
            fontStyle: p.data._role === 'note' ? 'italic' : undefined,
            color: p.data._role === 'note' ? '#8a5a1e' : undefined,
            padding: '0 4px',
            background: ROLE_COLORS[p.data._role] || '#fff',
            borderBottom: p.data._isLastRow ? '2px solid #9E9E9E' : undefined,
            cursor: (flagMode || linkMode || cancelMode) ? 'crosshair' : undefined,
          }
          if (p.data._role === 'final' && p.value !== null && p.value !== undefined) {
            const min = p.data._minStock, max = p.data._maxStock
            if (min > 0 && p.value < min) Object.assign(base, { background: '#FFCDD2', fontWeight: 700 })
            if (max > 0 && p.value > max) Object.assign(base, { background: '#C8E6C9', fontWeight: 700 })
          }
          const flag = cellFlags[flagKey(p.data._productId, p.data._rowTypeId, iso)]
          if (flag) {
            base.background = flag.color + '33'
            base.boxShadow = `inset 3px 0 0 ${flag.color}`
            base.fontWeight = 700
          }
          if (canceledCells[flagKey(p.data._productId, p.data._rowTypeId, iso)]) {
            base.textDecoration = 'line-through'
            base.color = '#999'
            base.background = '#eee'
          }
          if (pendingSource && pendingSource.productId === p.data._productId &&
              pendingSource.rowTypeId === p.data._rowTypeId && pendingSource.date === iso) {
            base.boxShadow = 'inset 0 0 0 2px #1565C0'
          }
          return base
        },
        valueFormatter: (p) => (p.value !== null && p.value !== undefined) ? String(p.value) : '',
        valueParser:    (p) => (p.newValue === '' || p.newValue === null) ? null : Number(p.newValue),
      })),
    }))

    return [...pinned, ...dateCols]
  }, [dates, flagMode, linkMode, cancelMode, cellFlags, canceledCells, pendingSource, onDateCellClicked, onProductClick, products])

  const onCellValueChanged = useCallback(async (event) => {
    const { data, colDef, newValue } = event
    const dateKey = colDef.field
    if (!dateKey?.match(/^\d{4}-\d{2}-\d{2}$/)) return
    try {
      const result = await updateValue({
        product_id:  data._productId,
        row_type_id: data._rowTypeId,
        date:        dateKey,
        value:       newValue === '' ? null : newValue,
      })
      onCellUpdated(result.updates)
    } catch (e) {
      console.error('Save failed', e)
    }
  }, [onCellUpdated])

  // Excel-style paste: no ag-Grid range selection (that's an Enterprise-only
  // module), so this pastes relative to whatever cell is currently focused —
  // click a starting cell, paste a block copied from Excel, and it fills
  // forward through the following date columns / grid rows from there.
  const [pasteStatus, setPasteStatus] = useState(null)  // {n} | {error}
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const onPaste = async (e) => {
      const api = gridRef.current?.api
      if (!api) return
      if (api.getEditingCells().length > 0) return  // let native single-cell paste happen
      const focused = api.getFocusedCell()
      if (!focused) return

      const text = e.clipboardData.getData('text/plain')
      if (!text) return
      e.preventDefault()

      const pasteRows = text.replace(/\r/g, '').split('\n').filter(r => r.length > 0).map(r => r.split('\t'))
      const allCols = api.getAllDisplayedColumns()
      const startColIdx = allCols.findIndex(c => c.getColId() === focused.column.getColId())
      if (startColIdx === -1) return

      const updates = []
      let skippedNonEditable = 0
      for (let i = 0; i < pasteRows.length; i++) {
        const rowNode = api.getDisplayedRowAtIndex(focused.rowIndex + i)
        if (!rowNode) break
        const editable = EDITABLE_ROLES.has(rowNode.data._role)
        if (!editable) { skippedNonEditable++; continue }
        for (let j = 0; j < pasteRows[i].length; j++) {
          const col = allCols[startColIdx + j]
          if (!col) break
          const colId = col.getColId()
          if (!/^\d{4}-\d{2}-\d{2}$/.test(colId)) continue  // skip pinned/non-date columns
          const raw = pasteRows[i][j].trim()
          if (raw === '') continue
          const num = Number(raw)
          if (Number.isNaN(num)) continue
          updates.push({ product_id: rowNode.data._productId, row_type_id: rowNode.data._rowTypeId, date: colId, value: num })
        }
      }

      if (!updates.length) {
        setPasteStatus({ error: skippedNonEditable ? '編集できない行のため貼り付けられませんでした' : '貼り付けるデータがありません' })
        setTimeout(() => setPasteStatus(null), 3000)
        return
      }
      try {
        await updateValuesBatch(updates)
        setPasteStatus({ n: updates.length })
      } catch (err) {
        console.error('Paste failed', err)
        setPasteStatus({ error: '貼り付けに失敗しました' })
      }
      setTimeout(() => setPasteStatus(null), 3000)
    }

    el.addEventListener('paste', onPaste)
    return () => el.removeEventListener('paste', onPaste)
  }, [])

  const defaultColDef = useMemo(() => ({
    sortable: false,
    resizable: true,
    suppressMovable: true,
  }), [])

  // ── SVG arrow overlay for lot_links, + edge-scroll loads more of the continuous timeline ──
  const extendLock = useRef(false)
  const recomputeOverlay = useCallback(() => {
    requestAnimationFrame(() => setOverlayTick(t => t + 1))
    if (extendLock.current || !onExtendRange) return
    const viewport = containerRef.current?.querySelector('.ag-center-cols-viewport')
    if (!viewport) return
    const { scrollLeft, scrollWidth, clientWidth } = viewport
    const nearEnd = scrollWidth - (scrollLeft + clientWidth) < clientWidth * 0.5
    const nearStart = scrollLeft < clientWidth * 0.5
    if (nearEnd || nearStart) {
      extendLock.current = true
      onExtendRange(nearStart && !nearEnd ? 'start' : 'end')
      setTimeout(() => { extendLock.current = false }, 1500)
    }
  }, [onExtendRange])

  useEffect(() => {
    const handle = () => recomputeOverlay()
    window.addEventListener('resize', handle)
    return () => window.removeEventListener('resize', handle)
  }, [recomputeOverlay])

  const getCellCenter = useCallback((productId, rowTypeId, date) => {
    if (!containerRef.current) return null
    const idx = rowIndexOf(productId, rowTypeId)
    if (idx === -1) return null
    const cell = containerRef.current.querySelector(
      `.ag-center-cols-container [row-index="${idx}"] [col-id="${date}"]`
    )
    if (!cell) return null
    const cellRect = cell.getBoundingClientRect()
    const containerRect = containerRef.current.getBoundingClientRect()
    return {
      x: cellRect.left - containerRect.left + cellRect.width / 2,
      y: cellRect.top - containerRect.top + cellRect.height / 2,
    }
  }, [rowIndexOf])

  const linkPaths = useMemo(() => {
    // eslint-disable-next-line no-unused-expressions
    overlayTick
    return lotLinks.map(link => {
      const from = getCellCenter(link.source_product_id, link.source_row_type_id, link.source_date)
      const to   = getCellCenter(link.target_product_id, link.target_row_type_id, link.target_date)
      if (!from || !to) return null
      const midY = (from.y + to.y) / 2
      const d = `M${from.x},${from.y} C${from.x},${midY} ${to.x},${midY} ${to.x},${to.y}`
      return { id: link.id, d, color: LINK_COLOR[link.link_type] || '#666', link }
    }).filter(Boolean)
  }, [lotLinks, getCellCenter, overlayTick])

  return (
    <div ref={containerRef} style={{ position: 'relative', flex: 1, width: '100%', display: 'flex' }}>
      <div className="ag-theme-alpine" style={{ flex: 1, width: '100%' }}>
        <AgGridReact
          ref={gridRef}
          rowData={rowData}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          onCellValueChanged={onCellValueChanged}
          onBodyScroll={recomputeOverlay}
          onGridSizeChanged={recomputeOverlay}
          onFirstDataRendered={recomputeOverlay}
          suppressRowTransform
          rowHeight={24}
          headerHeight={26}
          groupHeaderHeight={26}
          suppressScrollOnNewData
          suppressColumnVirtualisation={false}
          tooltipShowDelay={200}
        />
      </div>

      <svg style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 5 }}>
        <defs>
          {LINK_TYPES.map(t => (
            <marker key={t.id} id={`arrow-${t.id}`} markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <path d="M0,0 L6,3 L0,6 z" fill={t.color} />
            </marker>
          ))}
        </defs>
        {linkPaths.map(({ id, d, color, link }) => (
          <path key={id} d={d} stroke={color} strokeWidth={2} fill="none"
            markerEnd={`url(#arrow-${link.link_type})`}
            style={{ pointerEvents: 'stroke', cursor: 'pointer' }}
            onClick={() => { if (window.confirm('このリンクを削除しますか？')) onDeleteLink?.(id) }}>
            <title>{link.link_type}{link.quantity ? ` ${link.quantity}${link.conversion_factor !== 1 ? ` ×${link.conversion_factor}` : ''}` : ''}{link.note ? ` — ${link.note}` : ''}</title>
          </path>
        ))}
      </svg>

      {linkMode && pendingSource && (
        <div style={{ position: 'absolute', top: 8, left: 8, background: '#1565C0', color: '#fff',
          fontSize: 12, padding: '4px 10px', borderRadius: 5, zIndex: 10 }}>
          リンク元を選択済み — もう一つセルをクリックして接続先を選んでください
        </div>
      )}

      {pasteStatus && (
        <div style={{
          position: 'absolute', top: 8, right: 8, zIndex: 10,
          background: pasteStatus.error ? '#c0392b' : '#0f8a7a', color: '#fff',
          fontSize: 12, padding: '5px 12px', borderRadius: 5,
        }}>
          {pasteStatus.error || `${pasteStatus.n}件貼り付けました`}
        </div>
      )}

      {popover && (
        <FlagPopover
          popover={popover}
          onClose={() => setPopover(null)}
          onSet={async (color, note) => {
            await onSetFlag(popover.productId, popover.rowTypeId, popover.date, color, note)
            setPopover(null)
          }}
          onClear={async () => {
            await onClearFlag(popover.productId, popover.rowTypeId, popover.date)
            setPopover(null)
          }}
        />
      )}

      {linkModal && (
        <LinkModal
          modal={linkModal}
          onClose={() => setLinkModal(null)}
          onCreate={async (body) => {
            await onCreateLink({
              link_type: body.link_type,
              source_product_id: linkModal.source.productId,
              source_row_type_id: linkModal.source.rowTypeId,
              source_date: linkModal.source.date,
              target_product_id: linkModal.target.productId,
              target_row_type_id: linkModal.target.rowTypeId,
              target_date: linkModal.target.date,
              quantity: body.quantity,
              conversion_factor: body.conversion_factor,
              note: body.note,
            })
            setLinkModal(null)
          }}
        />
      )}
    </div>
  )
}

function FlagPopover({ popover, onClose, onSet, onClear }) {
  const [note, setNote] = useState(popover.existing?.note || '')
  return (
    <div style={{
      position: 'absolute', left: popover.x, top: popover.y, zIndex: 20,
      background: '#fff', border: '1px solid #ccc', borderRadius: 6, boxShadow: '0 4px 16px rgba(0,0,0,.15)',
      padding: 10, width: 200, fontSize: 12,
    }}>
      <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
        {FLAG_COLORS.map(c => (
          <button key={c} onClick={() => onSet(c, note)}
            style={{ width: 22, height: 22, borderRadius: '50%', background: c, border: '2px solid #fff', boxShadow: '0 0 0 1px #ccc', cursor: 'pointer' }} />
        ))}
      </div>
      <input value={note} onChange={e => setNote(e.target.value)} placeholder="メモ（任意）"
        style={{ width: '100%', fontSize: 12, padding: '4px 6px', border: '1px solid #ddd', borderRadius: 4, marginBottom: 8 }} />
      <div style={{ display: 'flex', gap: 6, justifyContent: 'space-between' }}>
        {popover.existing && (
          <button onClick={onClear} style={{ fontSize: 11, color: '#c0392b', background: 'none', border: 'none', cursor: 'pointer' }}>
            クリア
          </button>
        )}
        <button onClick={onClose} style={{ fontSize: 11, color: '#888', background: 'none', border: 'none', cursor: 'pointer', marginLeft: 'auto' }}>
          閉じる
        </button>
      </div>
    </div>
  )
}

function LinkModal({ modal, onClose, onCreate }) {
  const [linkType, setLinkType] = useState('reschedule')
  const [quantity, setQuantity] = useState('')
  const [conversion, setConversion] = useState('1')
  const [note, setNote] = useState('')

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.25)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }}
      onClick={onClose}>
      <div style={{ background: '#fff', borderRadius: 8, padding: 20, width: 320, fontSize: 13 }} onClick={e => e.stopPropagation()}>
        <div style={{ fontWeight: 700, marginBottom: 10 }}>リンクを作成</div>
        <div style={{ fontSize: 11, color: '#666', marginBottom: 10 }}>
          {modal.source.date} → {modal.target.date}
        </div>
        <label style={{ display: 'block', fontSize: 11, color: '#666', marginBottom: 4 }}>種類</label>
        <select value={linkType} onChange={e => setLinkType(e.target.value)}
          style={{ width: '100%', padding: '5px 6px', marginBottom: 10, border: '1px solid #ccc', borderRadius: 5 }}>
          {LINK_TYPES.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
        <label style={{ display: 'block', fontSize: 11, color: '#666', marginBottom: 4 }}>数量（任意）</label>
        <input type="number" value={quantity} onChange={e => setQuantity(e.target.value)}
          style={{ width: '100%', padding: '5px 6px', marginBottom: 10, border: '1px solid #ccc', borderRadius: 5 }} />
        {linkType === 'transfer' && (
          <>
            <label style={{ display: 'block', fontSize: 11, color: '#666', marginBottom: 4 }}>換算係数（例: 副素材＝X40する → 40）</label>
            <input type="number" value={conversion} onChange={e => setConversion(e.target.value)}
              style={{ width: '100%', padding: '5px 6px', marginBottom: 10, border: '1px solid #ccc', borderRadius: 5 }} />
          </>
        )}
        <label style={{ display: 'block', fontSize: 11, color: '#666', marginBottom: 4 }}>メモ（任意）</label>
        <input value={note} onChange={e => setNote(e.target.value)}
          style={{ width: '100%', padding: '5px 6px', marginBottom: 14, border: '1px solid #ccc', borderRadius: 5 }} />
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={{ padding: '6px 14px', border: 'none', borderRadius: 5, background: '#eee', cursor: 'pointer' }}>キャンセル</button>
          <button
            onClick={() => onCreate({
              link_type: linkType,
              quantity: quantity === '' ? null : Number(quantity),
              conversion_factor: conversion === '' ? 1 : Number(conversion),
              note: note || null,
            })}
            style={{ padding: '6px 14px', border: 'none', borderRadius: 5, background: '#1565C0', color: '#fff', cursor: 'pointer' }}>
            作成
          </button>
        </div>
      </div>
    </div>
  )
}

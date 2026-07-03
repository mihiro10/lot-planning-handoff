import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import PlanningGrid from './components/PlanningGrid'
import Sidebar from './components/Sidebar'
import Setup from './components/Setup'
import ProductPanel from './components/ProductPanel'
import BulkStocktakeScreen from './components/BulkStocktakeScreen'
import AnalysisDashboard from './components/AnalysisDashboard'
import WorkerView from './components/WorkerView'
import {
  getGrid, getRowTypes, createRowType, reorderRowTypes, connectWebSocket,
  getCellFlags, setCellFlag, clearCellFlag, getLotLinks, createLotLink, deleteLotLink,
  getCanceledCells, cancelCell, uncancelCell,
  getMonths,
} from './api'

const s = {
  header: { background: '#1565C0', color: '#fff', padding: '8px 16px', display: 'flex', alignItems: 'center', gap: 16, flexShrink: 0 },
  title:  { fontSize: 16, fontWeight: 700 },
  body:   { display: 'flex', flex: 1, overflow: 'hidden' },
  main:   { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' },
  status: { fontSize: 12, opacity: 0.8 },
  err:    { padding: 16, color: '#c00' },
  btn:    { background: 'rgba(255,255,255,.15)', color: '#fff', border: '1px solid rgba(255,255,255,.4)', borderRadius: 5, padding: '4px 10px', fontSize: 12, cursor: 'pointer' },
  btnActive: { background: '#fff', color: '#1565C0', border: '1px solid #fff', borderRadius: 5, padding: '4px 10px', fontSize: 12, cursor: 'pointer', fontWeight: 700 },
  nameInput: { fontSize: 12, padding: '3px 6px', borderRadius: 4, border: 'none', width: 90 },
}

function todayISO() { return new Date().toISOString().slice(0, 10) }
function addDays(iso, n) {
  const d = new Date(iso + 'T00:00:00')
  d.setDate(d.getDate() + n)
  return d.toISOString().slice(0, 10)
}
function flagKey(productId, rowTypeId, date) { return `${productId}:${rowTypeId}:${date}` }

export default function App() {
  const [range, setRange]                 = useState({ start: addDays(todayISO(), -60), end: addDays(todayISO(), 30) })
  const [gridData, setGridData]           = useState(null)
  const [rowTypes, setRowTypes]           = useState([])
  const [visibleRowTypes, setVisible]     = useState([])
  const [filters, setFilters]             = useState({})
  const [loading, setLoading]             = useState(true)
  const [needsSetup, setNeedsSetup]       = useState(false)
  const [error, setError]                 = useState(null)
  const [liveStatus, setLiveStatus]       = useState('接続中...')
  const [selectedProduct, setSelectedProduct] = useState(null)
  const [productRowOverrides, setProductRowOverrides] = useState({})
  const [cellFlags, setCellFlags]         = useState({})   // key -> flag
  const [lotLinks, setLotLinks]           = useState([])
  const [canceledCells, setCanceledCells] = useState({})   // key -> cancel entry
  const [flagMode, setFlagMode]           = useState(false)
  const [linkMode, setLinkMode]           = useState(false)
  const [cancelMode, setCancelMode]       = useState(false)
  const [showAnalysis, setShowAnalysis]   = useState(false)
  const [showBulkStocktake, setShowBulkStocktake] = useState(false)
  const [showWorkerView, setShowWorkerView] = useState(false)
  const [currentMonthId, setCurrentMonthId] = useState(null)
  const [editorName, setEditorName]       = useState(() => localStorage.getItem('lot_planning_editor_name') || '')
  const wsRef = useRef(null)

  useEffect(() => {
    localStorage.setItem('lot_planning_editor_name', editorName)
  }, [editorName])

  const loadGrid = useCallback(async (r = range) => {
    try {
      const [grid, rts, flags, links, canceled, months] = await Promise.all([
        getGrid(r.start, r.end), getRowTypes(), getCellFlags(r.start, r.end), getLotLinks(r.start, r.end),
        getCanceledCells(r.start, r.end), getMonths(),
      ])
      setGridData(grid)
      setRowTypes(rts)
      setVisible(rts.filter(x => x.is_visible_default).map(x => x.id))
      const flagMap = {}
      for (const f of flags) flagMap[flagKey(f.product_id, f.row_type_id, f.date)] = f
      setCellFlags(flagMap)
      setLotLinks(links)
      const cancelMap = {}
      for (const c of canceled) cancelMap[flagKey(c.product_id, c.row_type_id, c.date)] = c
      setCanceledCells(cancelMap)
      const today = todayISO()
      const cur = months.find(m => m.start_date <= today && m.end_date >= today) || months[0]
      setCurrentMonthId(cur?.id ?? null)

      const planners   = [...new Set(grid.products.flatMap(p => Array.isArray(p.planner) ? p.planner : []).filter(Boolean))]
      const locations  = [...new Set(grid.products.flatMap(p => Array.isArray(p.mfg_location) ? p.mfg_location : []).filter(Boolean))]
      const categories = [...new Set(grid.products.map(p => p.category).filter(Boolean))]
      setFilters(f => ({ ...f, _allPlanners: planners, _allLocations: locations, _allCategories: categories }))
      setError(null)
    } catch (e) {
      if (e.response?.status === 404) {
        setNeedsSetup(true)
      } else {
        setError('サーバーに接続できません。バックエンドが起動しているか確認してください。')
      }
    } finally {
      setLoading(false)
    }
  }, [range])

  useEffect(() => { loadGrid(range) }, [])  // eslint-disable-line

  // WebSocket real-time updates — shared across everyone editing, not a private diff
  useEffect(() => {
    const ws = connectWebSocket((msg) => {
      if (msg.type === 'cell_updates') {
        setGridData(prev => {
          if (!prev) return prev
          const inWindow = new Set(prev.dates)
          const next = { ...prev, products: prev.products.map(p => ({ ...p, values: { ...p.values } })) }
          for (const upd of msg.updates) {
            if (!inWindow.has(upd.date)) continue  // recompute spans full history; only keep what's in view
            const product = next.products.find(p => p.id === upd.product_id)
            if (!product) continue
            const key = String(upd.row_type_id)
            product.values[key] = { ...(product.values[key] || {}), [upd.date]: upd.value }
          }
          return next
        })
      } else if (msg.type === 'cell_flag_set') {
        const f = msg.flag
        setCellFlags(prev => ({ ...prev, [flagKey(f.product_id, f.row_type_id, f.date)]: f }))
      } else if (msg.type === 'cell_flag_cleared') {
        const f = msg.flag
        setCellFlags(prev => {
          const next = { ...prev }
          delete next[flagKey(f.product_id, f.row_type_id, f.date)]
          return next
        })
      } else if (msg.type === 'lot_link_created') {
        setLotLinks(prev => [...prev, msg.link])
      } else if (msg.type === 'lot_link_deleted') {
        setLotLinks(prev => prev.filter(l => l.id !== msg.id))
      } else if (msg.type === 'cell_canceled') {
        const c = msg.cancel
        setCanceledCells(prev => ({ ...prev, [flagKey(c.product_id, c.row_type_id, c.date)]: c }))
      } else if (msg.type === 'cell_uncanceled') {
        const c = msg.cancel
        setCanceledCells(prev => {
          const next = { ...prev }
          delete next[flagKey(c.product_id, c.row_type_id, c.date)]
          return next
        })
      }
    })
    wsRef.current = ws
    ws.onopen  = () => setLiveStatus('ライブ同期中')
    ws.onclose = () => setLiveStatus('接続切れ')
    return () => ws.close()
  }, [])

  const onCellUpdated = useCallback(() => {}, [])

  const onToggleRowType = useCallback((id) => {
    setVisible(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  }, [])

  const onFilterChange = useCallback((change) => {
    setFilters(prev => ({ ...prev, ...change }))
  }, [])

  const onAddRowType = useCallback(async (body) => {
    await createRowType(body)
    const rts = await getRowTypes()
    setRowTypes(rts)
    setVisible(prev => [...prev, rts[rts.length - 1].id])
    await loadGrid()
  }, [loadGrid])

  const onSetProductRows = useCallback((productId, ids) => {
    setProductRowOverrides(prev => ({ ...prev, [productId]: ids }))
  }, [])

  const onReorder = useCallback(async (orderedIds) => {
    await reorderRowTypes(orderedIds)
    const rts = await getRowTypes()
    setRowTypes(rts)
  }, [])

  // Grow the continuous window when the grid scrolls near an edge — no month copy, just more days
  const onExtendRange = useCallback((direction) => {
    setRange(prev => {
      const next = direction === 'start'
        ? { start: addDays(prev.start, -30), end: prev.end }
        : { start: prev.start, end: addDays(prev.end, 30) }
      loadGrid(next)
      return next
    })
  }, [loadGrid])

  const onSetFlag = useCallback(async (productId, rowTypeId, date, color, note) => {
    await setCellFlag({ product_id: productId, row_type_id: rowTypeId, date, color, note, set_by: editorName || null })
  }, [editorName])

  const onClearFlag = useCallback(async (productId, rowTypeId, date) => {
    await clearCellFlag({ product_id: productId, row_type_id: rowTypeId, date })
  }, [])

  // Canceling excludes the cell from 入庫予定数/最終; the raw value stays visible with a strikethrough
  const onToggleCancel = useCallback(async (productId, rowTypeId, date) => {
    const already = canceledCells[flagKey(productId, rowTypeId, date)]
    if (already) {
      await uncancelCell({ product_id: productId, row_type_id: rowTypeId, date })
    } else {
      await cancelCell({ product_id: productId, row_type_id: rowTypeId, date, canceled_by: editorName || null })
    }
  }, [canceledCells, editorName])

  const onCreateLink = useCallback(async (link) => {
    await createLotLink({ ...link, created_by: editorName || null })
  }, [editorName])

  const onDeleteLink = useCallback(async (id) => {
    await deleteLotLink(id)
  }, [])

  // Client-side filter on products
  const filteredGridData = useMemo(() => gridData ? {
    ...gridData,
    products: gridData.products.filter(p => {
      if (filters.q && !p.name.includes(filters.q)) return false
      if (filters.planner && !(Array.isArray(p.planner) ? p.planner : []).includes(filters.planner)) return false
      if (filters.mfg_location && !(Array.isArray(p.mfg_location) ? p.mfg_location : []).includes(filters.mfg_location)) return false
      if (filters.category && p.category !== filters.category) return false
      return true
    }),
  } : null, [gridData, filters])

  if (loading)     return <div style={{ padding: 32 }}>読み込み中...</div>
  if (error)       return <div style={s.err}>{error}</div>
  if (needsSetup)  return <Setup onComplete={() => { setNeedsSetup(false); setLoading(true); loadGrid() }} />

  return (
    <>
      <div style={s.header}>
        <span style={s.title}>ロット計画</span>
        {gridData && (
          <span style={s.status}>{range.start} 〜 {range.end}（連続表示）</span>
        )}

        <input style={s.nameInput} placeholder="あなたの名前" value={editorName}
          onChange={e => setEditorName(e.target.value)} title="編集の記名に使われます" />

        <button style={flagMode ? s.btnActive : s.btn} onClick={() => { setFlagMode(v => !v); setLinkMode(false); setCancelMode(false) }}>
          🎨 変更ハイライト
        </button>
        <button style={linkMode ? s.btnActive : s.btn} onClick={() => { setLinkMode(v => !v); setFlagMode(false); setCancelMode(false) }}>
          ↗ リンク作成
        </button>
        <button style={cancelMode ? s.btnActive : s.btn} onClick={() => { setCancelMode(v => !v); setFlagMode(false); setLinkMode(false) }}>
          ✕ 取り消し
        </button>
        <button style={showBulkStocktake ? s.btnActive : s.btn} onClick={() => { setShowBulkStocktake(v => !v); setShowAnalysis(false); setShowWorkerView(false) }}>月次棚卸し</button>
        <button style={showAnalysis ? s.btnActive : s.btn} onClick={() => { setShowAnalysis(v => !v); setShowBulkStocktake(false); setShowWorkerView(false) }}>在庫分析</button>
        <button style={showWorkerView ? s.btnActive : s.btn} onClick={() => { setShowWorkerView(v => !v); setShowAnalysis(false); setShowBulkStocktake(false) }}>現場ビュー</button>

        <span style={{ marginLeft: 'auto', ...s.status }}>● {liveStatus}</span>
      </div>

      {selectedProduct && (
        <ProductPanel
          product={selectedProduct}
          monthId={currentMonthId}
          rowTypes={rowTypes}
          visibleRowTypes={visibleRowTypes}
          productRowOverrides={productRowOverrides}
          onSetProductRows={onSetProductRows}
          onClose={() => setSelectedProduct(null)}
          onSaved={() => { setSelectedProduct(null); loadGrid() }}
        />
      )}

      <div style={s.body}>
        <Sidebar
          rowTypes={rowTypes}
          visibleRowTypes={visibleRowTypes}
          onToggleRowType={onToggleRowType}
          filters={filters}
          onFilterChange={onFilterChange}
          onAddRowType={onAddRowType}
          onReorder={onReorder}
        />
        <div style={s.main}>
          {showAnalysis ? (
            <AnalysisDashboard defaultRange={range} />
          ) : showBulkStocktake ? (
            <BulkStocktakeScreen products={gridData?.products || []} editorName={editorName} />
          ) : showWorkerView ? (
            gridData && <WorkerView gridData={gridData} />
          ) : filteredGridData && (
            <PlanningGrid
              gridData={filteredGridData}
              visibleRowTypes={visibleRowTypes}
              productRowOverrides={productRowOverrides}
              cellFlags={cellFlags}
              lotLinks={lotLinks}
              canceledCells={canceledCells}
              flagMode={flagMode}
              linkMode={linkMode}
              cancelMode={cancelMode}
              onCellUpdated={onCellUpdated}
              onProductClick={setSelectedProduct}
              onSetFlag={onSetFlag}
              onClearFlag={onClearFlag}
              onCreateLink={onCreateLink}
              onDeleteLink={onDeleteLink}
              onToggleCancel={onToggleCancel}
              onExtendRange={onExtendRange}
            />
          )}
        </div>
      </div>
    </>
  )
}

import React, { useState } from 'react'
import axios from 'axios'

const s = {
  overlay:  { position: 'fixed', inset: 0, background: '#f0f4f8', display: 'flex', alignItems: 'center', justifyContent: 'center' },
  card:     { background: '#fff', borderRadius: 10, boxShadow: '0 4px 24px rgba(0,0,0,.12)', width: 480, padding: 32 },
  title:    { fontSize: 20, fontWeight: 700, marginBottom: 4, color: '#1565C0' },
  sub:      { fontSize: 13, color: '#666', marginBottom: 24 },
  label:    { display: 'block', fontWeight: 600, fontSize: 12, marginBottom: 4, marginTop: 14, color: '#444' },
  row:      { display: 'flex', gap: 12 },
  input:    { flex: 1, padding: '7px 10px', border: '1px solid #ccc', borderRadius: 6, fontSize: 13 },
  dropzone: { border: '2px dashed #90CAF9', borderRadius: 8, padding: '24px 16px', textAlign: 'center',
               cursor: 'pointer', background: '#F5F9FF', color: '#1565C0', fontSize: 13, marginTop: 8 },
  preview:  { background: '#F5F5F5', borderRadius: 6, padding: 12, fontSize: 12, maxHeight: 180, overflowY: 'auto', marginTop: 8 },
  btnRow:   { display: 'flex', gap: 10, marginTop: 24, justifyContent: 'flex-end' },
  btn:      { padding: '8px 20px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 600 },
  err:      { color: '#c62828', fontSize: 12, marginTop: 8 },
  step:     { display: 'flex', gap: 0, marginBottom: 24 },
  stepItem: (active, done) => ({
    flex: 1, textAlign: 'center', fontSize: 12, paddingBottom: 8,
    borderBottom: `2px solid ${done ? '#43A047' : active ? '#1565C0' : '#ddd'}`,
    color: done ? '#43A047' : active ? '#1565C0' : '#999', fontWeight: active || done ? 700 : 400,
  }),
}

export default function Setup({ onComplete }) {
  const [step, setStep]           = useState(1)          // 1=month, 2=import
  const [year, setYear]           = useState(new Date().getFullYear())
  const [month, setMonth]         = useState(new Date().getMonth() + 1)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate]     = useState('')
  const [parsed, setParsed]       = useState(null)       // parse_excel result
  const [fileName, setFileName]   = useState('')
  const [error, setError]         = useState('')
  const [saving, setSaving]       = useState(false)

  // Auto-fill start/end when year/month changes
  const handleYearMonth = (y, m) => {
    const start = new Date(y, m - 1, 1)
    const end   = new Date(y, m, 0)  // last day of month
    // extend end by 13 days for overflow planning columns
    const extEnd = new Date(end); extEnd.setDate(extEnd.getDate() + 13)
    setStartDate(start.toISOString().slice(0, 10))
    setEndDate(extEnd.toISOString().slice(0, 10))
  }

  const onYearChange  = (e) => { setYear(Number(e.target.value));  handleYearMonth(Number(e.target.value), month) }
  const onMonthChange = (e) => { setMonth(Number(e.target.value)); handleYearMonth(year, Number(e.target.value)) }

  const onFileChange = async (file) => {
    if (!file) return
    setFileName(file.name)
    setError('')
    setParsed(null)
    const form = new FormData()
    form.append('file', file)
    try {
      const res = await axios.post('/api/import/excel', form, { headers: { 'Content-Type': 'multipart/form-data' } })
      const data = res.data
      setParsed(data)
      // Auto-detect year/month from file's start_date
      if (data.start_date) {
        const d = new Date(data.start_date)
        const y = d.getFullYear(), m = d.getMonth() + 1
        setYear(y); setMonth(m)
        setStartDate(data.start_date)
        setEndDate(data.end_date)
      }
    } catch (e) {
      setError(e.response?.data?.detail || 'ファイルの読み込みに失敗しました。')
    }
  }

  const handleDrop = (e) => {
    e.preventDefault()
    const file = e.dataTransfer.files[0]
    if (file) onFileChange(file)
  }

  const commitImport = async () => {
    setSaving(true)
    setError('')
    try {
      await axios.post('/api/import/commit', {
        year, month, start_date: startDate, end_date: endDate,
        products: parsed ? parsed.products : [],
      })
      onComplete()
    } catch (e) {
      setError(e.response?.data?.detail || '保存に失敗しました。')
    } finally {
      setSaving(false)
    }
  }

  const skipImport = async () => {
    setSaving(true)
    setError('')
    try {
      await axios.post('/api/import/commit', {
        year, month, start_date: startDate, end_date: endDate, products: [],
      })
      onComplete()
    } catch (e) {
      setError(e.response?.data?.detail || '保存に失敗しました。')
    } finally {
      setSaving(false)
    }
  }

  // Init date on first render
  React.useEffect(() => { handleYearMonth(year, month) }, [])  // eslint-disable-line

  return (
    <div style={s.overlay}>
      <div style={s.card}>
        <div style={s.title}>ロット計画 セットアップ</div>
        <div style={s.sub}>最初に計画する月を設定してください。</div>

        {/* Step indicator */}
        <div style={s.step}>
          <div style={s.stepItem(step === 1, step > 1)}>① 月の設定</div>
          <div style={s.stepItem(step === 2, false)}>② Excelから読み込み</div>
        </div>

        {step === 1 && (
          <>
            <label style={s.label}>計画年月</label>
            <div style={s.row}>
              <input style={s.input} type="number" value={year} min={2020} max={2099}
                onChange={onYearChange} placeholder="年" />
              <select style={s.input} value={month} onChange={onMonthChange}>
                {[...Array(12)].map((_, i) => (
                  <option key={i + 1} value={i + 1}>{i + 1}月</option>
                ))}
              </select>
            </div>

            <label style={s.label}>開始日</label>
            <input style={{ ...s.input, flex: 'none', width: '100%' }} type="date"
              value={startDate} onChange={e => setStartDate(e.target.value)} />

            <label style={s.label}>終了日（オーバーフロー含む）</label>
            <input style={{ ...s.input, flex: 'none', width: '100%' }} type="date"
              value={endDate} onChange={e => setEndDate(e.target.value)} />

            {error && <div style={s.err}>{error}</div>}
            <div style={s.btnRow}>
              <button style={{ ...s.btn, background: '#1565C0', color: '#fff' }}
                disabled={!startDate || !endDate}
                onClick={() => setStep(2)}>
                次へ →
              </button>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <div
              style={s.dropzone}
              onDrop={handleDrop}
              onDragOver={e => e.preventDefault()}
              onClick={() => document.getElementById('xlsx-input').click()}
            >
              {fileName
                ? `📄 ${fileName}`
                : '既存のExcelファイルをドラッグ＆ドロップ、またはクリックして選択'}
              <input id="xlsx-input" type="file" accept=".xlsx" hidden
                onChange={e => onFileChange(e.target.files[0])} />
            </div>

            {parsed && (
              <div style={s.preview}>
                <strong>読み込み結果:</strong> {parsed.products.length}件の商品
                （{parsed.start_date} 〜 {parsed.end_date}）<br />
                {parsed.products.slice(0, 8).map(p => (
                  <div key={p.name} style={{ marginTop: 3 }}>
                    ✓ {p.name}{p.code ? ` [${p.code}]` : ''} — 在庫 {p.starting_inventory}
                  </div>
                ))}
                {parsed.products.length > 8 && (
                  <div style={{ color: '#666', marginTop: 4 }}>…他 {parsed.products.length - 8}件</div>
                )}
              </div>
            )}

            {error && <div style={s.err}>{error}</div>}

            <div style={s.btnRow}>
              <button style={{ ...s.btn, background: '#eee', color: '#333' }}
                onClick={() => setStep(1)}>← 戻る</button>
              <button style={{ ...s.btn, background: '#757575', color: '#fff' }}
                disabled={saving} onClick={skipImport}>
                スキップ（空白で開始）
              </button>
              <button style={{ ...s.btn, background: '#2E7D32', color: '#fff' }}
                disabled={saving || !parsed}
                onClick={commitImport}>
                {saving ? '保存中...' : `${parsed?.products.length ?? 0}件を読み込む`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

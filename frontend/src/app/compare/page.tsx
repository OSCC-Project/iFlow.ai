'use client'
import { useState, useEffect, useRef } from 'react'
import dynamic from 'next/dynamic'
const CompareChart = dynamic(() => import('@/components/CompareChart'), { ssr: false })

import { withToken } from '@/lib/authFetch'

const API = 'http://localhost:8000'

interface VarConfig { id: string; type: string; values: string }

const VAR_TYPES = ['工艺 (PDK)', '利用率', '目标频率 (MHz)', '设计', '设计版本', '工具参数']
// 变量类型 → config key 映射 (与后端 api_flow_run_internal 对齐)
const VAR_KEYS: Record<string, string> = {
  '工艺 (PDK)': 'PDK', '利用率': 'utilization', '目标频率 (MHz)': 'frequency', '设计': 'design',
}

type Progress = Record<string, { step: string; status: string; log: string }>

// 下拉多选控件 (方案 5.2.2 复选框列表风格): 收起时显示已选值, 展开为复选清单
function MultiSelect({ options, selected, onToggle, onClear, placeholder }: {
  options: string[]; selected: string[]; onToggle: (v: string) => void;
  onClear: () => void; placeholder: string
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen(!open)}
        className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-left flex items-center justify-between gap-1 hover:border-gray-600">
        <span className={`truncate ${selected.length ? 'text-gray-200' : 'text-gray-600'}`}>
          {selected.length ? selected.join(', ') : placeholder}
        </span>
        <span className="text-gray-500 shrink-0">{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute z-20 mt-1 w-full bg-gray-800 border border-gray-700 rounded shadow-lg py-1">
            {options.map(o => (
              <label key={o} className="flex items-center gap-2 px-2 py-1 hover:bg-gray-700/50 cursor-pointer">
                <input type="checkbox" checked={selected.includes(o)}
                  onChange={() => onToggle(o)} className="accent-blue-500" />
                <span className="text-xs text-gray-300 font-mono">{o}</span>
              </label>
            ))}
            <div className="border-t border-gray-700 mt-1 pt-1 px-2 pb-1 flex items-center justify-between">
              <span className="text-[10px] text-gray-500">{selected.length} 项已选</span>
              {selected.length > 0 && (
                <button type="button" onClick={() => { onClear(); setOpen(false) }}
                  className="text-[10px] text-gray-500 hover:text-red-400">清空</button>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// 组合的唯一键 (行勾选/详情展开按它定位, 与排序无关)
const comboKey = (row: any) => JSON.stringify(row.combo || {})

// 区域 E: 同工艺纵向结论 — 把只在一个变量 (非设计/PDK) 上不同的行归组
function buildVerticalGroups(rows: any[]) {
  const groups: { axis: string; rows: any[] }[] = []
  const used = new Set<number>()
  for (let i = 0; i < rows.length; i++) {
    if (used.has(i)) continue
    const a = rows[i], ka = a.combo || {}
    let axis = ''
    const group = [a]
    used.add(i)
    for (let j = i + 1; j < rows.length; j++) {
      if (used.has(j)) continue
      const b = rows[j], kb = b.combo || {}
      const keys = new Set([...Object.keys(ka), ...Object.keys(kb)])
      const diff = [...keys].filter(k => String(ka[k] ?? '') !== String(kb[k] ?? ''))
      if (diff.length !== 1) continue
      const k = diff[0]
      if (k === 'design' || k === 'PDK') continue
      if (!axis) axis = k
      if (k !== axis) continue
      // 候选与组内所有成员只允许在 axis 上不同
      const ok = group.every(m => {
        const mk = m.combo || {}
        const mkeys = new Set([...Object.keys(kb), ...Object.keys(mk)])
        const d = [...mkeys].filter(kk => String(kb[kk] ?? '') !== String(mk[kk] ?? ''))
        return d.every(kk => kk === axis)
      })
      if (ok) { group.push(b); used.add(j) }
    }
    if (group.length >= 2) {
      // 组内按 axis 值排序 (数值感知, 如 30% < 35% < 40%)
      const numAware = (x: string, y: string) => {
        const nx = parseFloat(x), ny = parseFloat(y)
        if (!isNaN(nx) && !isNaN(ny)) return nx - ny
        return String(x).localeCompare(String(y))
      }
      const sorted = [...group].sort((x, y) =>
        numAware(String(x.combo?.[axis] ?? ''), String(y.combo?.[axis] ?? '')))
      groups.push({ axis, rows: sorted })
    }
  }
  return groups
}

export default function Compare() {
  const [design, setDesign] = useState('gcd')
  const [variables, setVariables] = useState<VarConfig[]>([
    { id: '1', type: '工艺 (PDK)', values: 'sky130, nangate45' },
    { id: '2', type: '利用率', values: '35%, 30%' },
  ])
  const [running, setRunning] = useState(false)
  const [results, setResults] = useState<any>(null)
  const [progress, setProgress] = useState<Progress>({})
  const [metric, setMetric] = useState<'wns'|'area'|'drc'>('area')
  const [maps, setMaps] = useState<any>(null)
  const [sortCol, setSortCol] = useState('')
  const [sortDir, setSortDir] = useState<1|-1>(1)
  // 行勾选 (comboKey 集合) + 区域 D 详情展开的当前组合
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set())
  const [detailKey, setDetailKey] = useState<string | null>(null)
  // 用户自主添加: 设计 RTL 上传 + 工艺 liberty 上传 (PPA-only)
  const [designUploads, setDesignUploads] = useState<Record<string, string>>({})
  const [libUploads, setLibUploads] = useState<Record<string, string>>({})
  const wsRefs = useRef<WebSocket[]>([])

  const onUploadDesigns = async (files: FileList | null) => {
    if (!files) return
    const next = { ...designUploads }
    for (const f of Array.from(files)) {
      if (!f.name.endsWith('.v')) continue
      next[f.name.replace(/\.v$/, '')] = await f.text()
    }
    setDesignUploads(next)
  }
  const onUploadLibs = async (files: FileList | null) => {
    if (!files) return
    const next = { ...libUploads }
    for (const f of Array.from(files)) {
      if (!f.name.endsWith('.lib')) continue
      next[f.name.replace(/\.lib$/, '')] = await f.text()
    }
    setLibUploads(next)
  }

  useEffect(() => () => { wsRefs.current.forEach(w => w.close()) }, [])

  const addVar = () => setVariables([...variables, { id: Date.now().toString(), type: '工艺 (PDK)', values: '' }])
  const removeVar = (id: string) => setVariables(variables.filter(v => v.id !== id))
  const comboCount = variables.reduce((n, v) => n * (v.values.split(',').filter(Boolean).length || 1), 1)

  // 设计变量的候选值: 固定设计 + 用户上传的设计 (上传后立即可选)
  const allDesigns = ['gcd', 'aes_cipher_top', 'uart', ...Object.keys(designUploads)]
  // 点击候选 chip → 在"设计"变量值里切换
  const toggleDesignValue = (varId: string, name: string) => {
    setVariables(variables.map(v => {
      if (v.id !== varId) return v
      const cur = v.values.split(',').map(s => s.trim()).filter(Boolean)
      const next = cur.includes(name) ? cur.filter(x => x !== name) : [...cur, name]
      return { ...v, values: next.join(', ') }
    }))
  }

  const runExperiment = async () => {
    setRunning(true); setResults(null); setProgress({})
    setSelectedRows(new Set()); setDetailKey(null)
    try {
      // 变量名翻译为后端 config key (如 "工艺 (PDK)" → "PDK")
      const vars: any = {}
      variables.forEach(v => {
        if (!v.values.trim()) return
        const key = VAR_KEYS[v.type] || v.type
        vars[key] = v.values
      })
      const r1 = await fetch(`${API}/api/experiment/create`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ design, variables: vars,
          design_uploads: designUploads, liberty_uploads: libUploads })
      })
      const exp = await r1.json()

      // P1-6/P0-3: 每个组合建立 WS, 实时显示步骤进度
      wsRefs.current.forEach(w => w.close())
      wsRefs.current = (exp.combos || []).map((c: any) => {
        const ws = new WebSocket(`ws://localhost:8000/ws/exp_${exp.id}_${c.id}`)
        ws.onmessage = (e) => {
          try {
            const ev = JSON.parse(e.data)
            if (ev.type === 'step_start') {
              setProgress(p => ({ ...p, [c.id]: { step: ev.step, status: 'running', log: '▶ ' + ev.step } }))
            } else if (ev.type === 'step_done') {
              const icon = ev.status === 'failed' ? '❌' : ev.status === 'skipped' ? '⏭️' : '✅'
              setProgress(p => ({ ...p, [c.id]: { step: ev.step, status: ev.status, log: `${icon} ${ev.step} (${ev.duration}s)` } }))
            }
          } catch {}
        }
        return ws
      })

      const r2 = await fetch(`${API}/api/experiment/${exp.id}/run`, { method: 'POST' })
      const data = await r2.json()
      setResults(data)
      // 区域 C: 空间 Map (统一色标密度热力图)
      try {
        const rm = await fetch(`${API}/api/experiment/${exp.id}/maps`)
        setMaps(await rm.json())
      } catch { setMaps(null) }
    } catch (e: any) { setResults({ error: String(e.message) }) }
    setRunning(false)
  }

  const rows: any[] = results?.summary?.rows || []
  const chartData = rows.map((r: any) => ({
    name: Object.entries(r.combo || {}).map(([k, v]) => `${k}=${v}`).join(' / ') || 'combo',
    wns: typeof r.wns_ns === 'number' ? r.wns_ns : null,
    area: typeof r.area_mm2 === 'number' ? r.area_mm2 : null,
    drc: typeof r.drc_violations === 'number' ? r.drc_violations : null,
    lint: r.lint_violations,
  }))

  // 区域 D: 组合 config → 完整步骤明细 (与 rows 同源, 后端同一对象序列化)
  const detailMap = new Map<string, any>(
    (results?.experiment?.results || []).map((r: any) => [JSON.stringify(r.config || {}), r]))
  const detailEntry = detailKey ? detailMap.get(detailKey) : null

  // 区域 A: 列排序 (点击表头切换)
  const sortRows = (col: string) => {
    if (sortCol === col) setSortDir(sortDir === 1 ? -1 : 1)
    else { setSortCol(col); setSortDir(1) }
  }
  const sortedRows = [...rows].sort((a, b) => {
    if (!sortCol) return 0
    const va = a[sortCol], vb = b[sortCol]
    if (va === vb) return 0
    if (va === null || va === undefined) return 1
    if (vb === null || vb === undefined) return -1
    const na: any = typeof va === 'number' ? va : String(va)
    const nb: any = typeof vb === 'number' ? vb : String(vb)
    return (na > nb ? 1 : -1) * sortDir
  })

  // 单元格颜色编码: WNS 越高越绿 / DRC 越少越绿 / 面积越小越绿 (相对本列 min-max)
  const cellCls = (col: string, v: any): string => {
    if (v === null || v === undefined) return 'text-gray-600'
    if (col === 'wns_ns') return typeof v === 'number' ? (v >= 0 ? 'text-green-400' : 'text-red-400') : ''
    if (col === 'drc_violations') return typeof v === 'number' ? (v === 0 ? 'text-green-400' : 'text-red-400') : ''
    if (col === 'area_mm2' && typeof v === 'number') {
      const vals = rows.map((r: any) => r[col]).filter((x: any) => typeof x === 'number')
      if (vals.length < 2) return ''
      const lo = Math.min(...vals), hi = Math.max(...vals)
      return v <= lo + (hi - lo) * 0.25 ? 'text-green-400' : v >= lo + (hi - lo) * 0.75 ? 'text-red-400' : 'text-yellow-400'
    }
    return ''
  }

  // 导出: 有勾选时只导出勾选行
  const exportRows = () => selectedRows.size ? rows.filter(r => selectedRows.has(comboKey(r))) : rows

  const cellText = (v: any) => v === null || v === undefined ? '' :
    typeof v === 'object' ? JSON.stringify(v).replace(/"/g, "'") : String(v)

  const exportCSV = () => {
    const cols: string[] = results?.summary?.columns || []
    const lines = [cols.join(',')]
    exportRows().forEach((r: any) => lines.push(cols.map((c: string) => cellText(r[c])).join(',')))
    const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob); a.download = `compare_${design}_${Date.now()}.csv`; a.click()
  }

  // Excel 导出: SpreadsheetML (.xls, Excel/WPS 直接打开, 无额外依赖)
  const exportXLS = () => {
    const cols: string[] = results?.summary?.columns || []
    const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    const cell = (v: any) => `<Cell><Data ss:Type="String">${esc(cellText(v))}</Data></Cell>`
    const head = `<Row>${cols.map(c => cell(c)).join('')}</Row>`
    const body = exportRows().map(r => `<Row>${cols.map(c => cell(r[c])).join('')}</Row>`).join('')
    const xml = `<?xml version="1.0"?><?mso-application progid="Excel.Sheet"?>` +
      `<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">` +
      `<Worksheet ss:Name="compare"><Table>${head}${body}</Table></Worksheet></Workbook>`
    const blob = new Blob(['﻿' + xml], { type: 'application/vnd.ms-excel' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob); a.download = `compare_${design}_${Date.now()}.xls`; a.click()
  }

  const toggleRow = (k: string) => setSelectedRows(prev => {
    const n = new Set(prev)
    if (n.has(k)) n.delete(k); else n.add(k)
    return n
  })
  const allSelected = rows.length > 0 && selectedRows.size === rows.length
  const toggleAll = () => setSelectedRows(allSelected ? new Set() : new Set(rows.map(comboKey)))

  // 区域 E: 同工艺纵向结论 (同设计同工艺, 单变量变化趋势)
  const verticalGroups = buildVerticalGroups(rows)
  const metricCols = [
    { key: 'wns_ns', label: 'WNS (ns)' }, { key: 'area_mm2', label: '面积 (mm²)' },
    { key: 'drc_violations', label: 'DRC' }, { key: 'power_mw', label: '功耗 (mW)' },
  ]
  const groupMetricCols = (g: { axis: string; rows: any[] }) =>
    metricCols.filter(mc => g.rows.some(r => typeof r[mc.key] === 'number'))

  const metricOptions = [
    { key: 'area', label: '面积 (μm²)', color: '#34d399' },
    { key: 'wns', label: 'WNS (ns)', color: '#60a5fa' },
    { key: 'drc', label: 'DRC 违规数', color: '#f87171' },
  ] as const

  // 区域 D 步骤指标格式化
  const metricUnits: Record<string, string> = { wns: 'ns', area: 'mm²', power: 'mW', drc: '处' }
  const fmtStepMetrics = (s: any): string => {
    const parts: string[] = []
    for (const [k, v] of Object.entries(s.metrics || {})) {
      if (v === null || v === undefined) continue
      parts.push(`${k} ${v}${metricUnits[k] || ''}`)
    }
    if (typeof s.violations === 'number') parts.push(`lint违规 ${s.violations}`)
    return parts.join(' · ') || '-'
  }
  const stepIcon = (s: any) => s.status === 'failed' ? '❌' : s.status === 'skipped' ? '⏭️' : '✅'
  const basename = (p: string) => (p || '').split('/').pop()

  return (
    <div className="max-w-6xl mx-auto space-y-4">
      <h2 className="text-lg font-bold text-blue-400">📊 对比实验</h2>

      {/* Config */}
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-3">
          {/* 用户自主添加: 上传设计 RTL / 上传工艺 liberty (PPA-only) */}
          <div className="space-y-2 bg-gray-800/30 rounded p-2">
            <div>
              <label className="text-xs text-gray-500 block mb-1">⬆ 上传设计 (.v, 可多选) — 加入「设计」变量候选</label>
              <input type="file" multiple accept=".v" onChange={e => onUploadDesigns(e.target.files)}
                className="text-[11px] text-gray-400 file:bg-gray-700 file:border-0 file:rounded file:px-2 file:py-1 file:text-gray-300 file:mr-2"/>
              {Object.keys(designUploads).length > 0 && (
                <div className="text-[10px] text-green-400 mt-1">已添加: {Object.keys(designUploads).join(', ')}</div>
              )}
            </div>
            <div>
              <label className="text-xs text-gray-500 block mb-1">⬆ 上传工艺 liberty (.lib, 可多选) — 在「工艺 (PDK)」变量里填同名做 PPA 对比</label>
              <input type="file" multiple accept=".lib" onChange={e => onUploadLibs(e.target.files)}
                className="text-[11px] text-gray-400 file:bg-gray-700 file:border-0 file:rounded file:px-2 file:py-1 file:text-gray-300 file:mr-2"/>
              {Object.keys(libUploads).length > 0 && (
                <div className="text-[10px] text-blue-400 mt-1">已添加: {Object.keys(libUploads).join(', ')} (综合+STA 对比, 无物理流程)</div>
              )}
            </div>
          </div>

          {variables.map(v => (
            <div key={v.id} className="flex gap-2 items-end">
              <div className="flex-1">
                <label className="text-xs text-gray-500 block mb-1">变量类型</label>
                <select value={v.type} onChange={e => {
                  setVariables(variables.map(x => x.id===v.id ? {...x, type: e.target.value} : x))
                }} className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs w-full">
                  {VAR_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div className="flex-1">
                {v.type === '设计' ? (
                  <>
                    <label className="text-xs text-gray-500 block mb-1">值</label>
                    <MultiSelect
                      options={allDesigns}
                      selected={v.values.split(',').map(s => s.trim()).filter(Boolean)}
                      onToggle={name => toggleDesignValue(v.id, name)}
                      onClear={() => setVariables(variables.map(x => x.id === v.id ? { ...x, values: '' } : x))}
                      placeholder="选择设计..." />
                  </>
                ) : (
                  <>
                    <label className="text-xs text-gray-500 block mb-1">值 (逗号分隔)</label>
                    <input value={v.values} onChange={e => {
                      setVariables(variables.map(x => x.id===v.id ? {...x, values: e.target.value} : x))
                    }} className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs w-full"
                      placeholder="sky130, nangate45" />
                  </>
                )}
              </div>
              <button onClick={() => removeVar(v.id)} className="text-red-400 text-xs pb-1">✕</button>
            </div>
          ))}

          <button onClick={addVar} className="text-blue-400 text-xs">+ 添加变量</button>
        </div>

        {/* Summary */}
        <div className="bg-gray-900 border border-gray-700 rounded p-4">
          <h4 className="text-sm font-medium mb-3 text-gray-300">实验摘要</h4>
          <div className="space-y-2 text-xs text-gray-400">
            <div>默认设计: <span className="text-gray-200">{design}</span> <span className="text-gray-600">(加「设计」变量可多设计对比)</span></div>
            <div>变量数: {variables.length}</div>
            <div>组合总数: <span className="text-blue-400 text-lg font-bold">{comboCount}</span></div>
            <div>预计耗时: ~{comboCount * 10} 分钟</div>
          </div>
          <button onClick={runExperiment} disabled={running}
            className="mt-3 bg-green-600 px-4 py-1.5 rounded text-sm w-full disabled:opacity-50">
            {running ? '运行中...' : '▶ 开始实验'}
          </button>
        </div>
      </div>

      {/* 运行进度 — 每组合实时步骤状态 (WS) */}
      {(running || Object.keys(progress).length > 0) && (
        <div className="bg-gray-900 border border-gray-700 rounded p-3">
          <h4 className="text-sm font-medium mb-2 text-gray-300">⚡ 实验进度</h4>
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(progress).map(([cid, p]) => (
              <div key={cid} className="flex items-center gap-2 text-[11px] bg-gray-800/50 rounded px-2 py-1.5">
                <span className="text-gray-500 w-20 truncate">{cid}</span>
                <span className={`${p.status==='failed'?'text-red-400':p.status==='skipped'?'text-yellow-500':'text-gray-300'} truncate flex-1`}>{p.log}</span>
                {p.status === 'running' && <span className="text-blue-400 animate-pulse">●</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 区域 B: 柱状图 */}
      {chartData.length > 0 && (
        <div className="bg-gray-900 border border-gray-700 rounded p-3">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-medium text-gray-300">📊 横向对比 ({metricOptions.find(m=>m.key===metric)?.label})</h4>
            <div className="flex gap-1">
              {metricOptions.map(m => (
                <button key={m.key} onClick={() => setMetric(m.key)}
                  className={`px-2 py-0.5 rounded text-[11px] ${metric===m.key?'bg-blue-600':'bg-gray-800 text-gray-400 hover:bg-gray-700'}`}>
                  {m.label}
                </button>
              ))}
            </div>
          </div>
          <CompareChart data={chartData} metric={metric} />
        </div>
      )}

      {/* 区域 A: 总表 (行勾选 + 点击行展开区域 D 详情) */}
      {results && !results.error && (
        <div className="bg-gray-900 border border-gray-700 rounded p-3">
          <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
            <h4 className="text-sm font-medium text-gray-300">对比结果 ({rows.length} 组)
              <span className="text-[10px] text-gray-600 ml-2">☑ 勾选行 · 点击行看步骤明细</span>
            </h4>
            <div className="flex items-center gap-3">
              {selectedRows.size > 0 && (
                <span className="text-[10px] text-gray-500">已选 {selectedRows.size} 行</span>
              )}
              <button onClick={exportCSV} className="text-blue-400 text-[11px] hover:underline">
                导出 CSV{selectedRows.size > 0 ? ` (${selectedRows.size})` : ''}
              </button>
              <button onClick={exportXLS} className="text-blue-400 text-[11px] hover:underline">
                导出 Excel{selectedRows.size > 0 ? ` (${selectedRows.size})` : ''}
              </button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-400 border-b border-gray-800">
                  <th className="w-8 py-1.5 px-1">
                    <input type="checkbox" checked={allSelected} onChange={toggleAll}
                      className="accent-blue-500" />
                  </th>
                  {results.summary?.columns?.map((c: string) => (
                    <th key={c} onClick={() => sortRows(c)}
                      className="text-left py-1.5 px-2 whitespace-nowrap cursor-pointer hover:text-white select-none">
                      {c}{sortCol === c ? (sortDir === 1 ? ' ↑' : ' ↓') : ''}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sortedRows.map((row: any, i: number) => {
                  const k = comboKey(row)
                  return (
                    <tr key={i} onClick={() => setDetailKey(detailKey === k ? null : k)}
                      className={`border-b border-gray-800/50 cursor-pointer ${detailKey === k ? 'bg-blue-900/30' : 'hover:bg-gray-800/30'}`}>
                      <td className="py-1 px-1" onClick={e => e.stopPropagation()}>
                        <input type="checkbox" checked={selectedRows.has(k)}
                          onChange={() => toggleRow(k)} className="accent-blue-500" />
                      </td>
                      {results.summary.columns.map((c: string) => (
                        <td key={c} className={`py-1 px-2 whitespace-nowrap ${cellCls(c, row[c])}`}>
                          {typeof row[c] === 'object' ? JSON.stringify(row[c]) : String(row[c] ?? '-')}
                        </td>
                      ))}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* 区域 D: 单设计详情 — 点行展开该组合的完整步骤明细 */}
          {detailKey && (
            <div className="mt-3 border border-blue-800/60 rounded p-3 bg-blue-950/10">
              <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
                <h4 className="text-sm font-medium text-blue-300">🔍 组合详情
                  <span className="text-gray-400 font-normal ml-2 text-[11px] font-mono">
                    {detailEntry ? Object.entries(detailEntry.config || {}).map(([k, v]) => `${k}=${v}`).join('  ') : detailKey}
                  </span>
                </h4>
                <button onClick={() => setDetailKey(null)} className="text-gray-500 text-[11px] hover:text-white">✕ 关闭</button>
              </div>
              {!detailEntry ? (
                <div className="text-[11px] text-gray-500">该组合执行失败或无步骤数据</div>
              ) : !detailEntry.result ? (
                <div className="text-[11px] text-red-400">组合执行失败: {detailEntry.error || '未知错误'}</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="text-gray-500 border-b border-gray-800">
                        <th className="text-left py-1.5 px-2">步骤</th>
                        <th className="text-left py-1.5 px-2 w-12">状态</th>
                        <th className="text-left py-1.5 px-2 w-20">耗时</th>
                        <th className="text-left py-1.5 px-2">关键指标</th>
                        <th className="text-left py-1.5 px-2">说明</th>
                        <th className="text-left py-1.5 px-2">产物</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(detailEntry.result.results || []).map((s: any, si: number) => (
                        <tr key={si} className="border-b border-gray-800/50">
                          <td className="py-1 px-2 text-gray-300 font-mono whitespace-nowrap">{s.step}</td>
                          <td className="py-1 px-2">{stepIcon(s)}
                            {s.status === 'running' && <span className="text-blue-400 animate-pulse">●</span>}
                          </td>
                          <td className="py-1 px-2 text-gray-500">{(s.duration ?? 0).toFixed(1)}s</td>
                          <td className="py-1 px-2 text-gray-300 font-mono">{fmtStepMetrics(s)}</td>
                          <td className="py-1 px-2 text-gray-500" title={s.reason || s.error || ''}>
                            {(s.reason || s.error || '').slice(0, 80)}
                            {((s.reason || '') + (s.error || '')).length > 80 ? '…' : ''}
                          </td>
                          <td className="py-1 px-2 font-mono text-gray-500">
                            {[s.def_path, s.gds_path].filter(Boolean).map((p: string) => (
                              <div key={p} className="truncate max-w-[180px]" title={p}>{basename(p)}</div>
                            ))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="text-[10px] text-gray-600 mt-1 font-mono">run_id: {detailEntry.result.run_id || '-'} · tool: {detailEntry.result.tool || '-'}</div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* 区域 C: 空间 Map 横向对比 — 统一色标密度热力图 */}
      {maps && (maps.maps || []).length > 0 && (
        <div className="bg-gray-900 border border-gray-700 rounded p-3">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-medium text-gray-300">🗺 空间 Map 横向对比 (Std-cell 密度)</h4>
            <span className="text-[10px] text-gray-500">统一色标 · vmax={maps.vmax} 单元/格 · {maps.grid}×{maps.grid} 网格</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            {maps.maps.map((m: any, i: number) => (
              <div key={i} className="bg-gray-800/40 rounded p-1.5">
                <div className="text-[10px] text-gray-500 mb-1 truncate">
                  {Object.entries(m.config || {}).map(([k, v]) => `${k}=${v}`).join(' ')}
                </div>
                {m.png ? (
                  <img src={withToken(`${API}/api/files/download?path=${encodeURIComponent(m.png)}`)}
                    className="w-full rounded" alt="density map" />
                ) : (
                  <div className="h-20 flex items-center justify-center text-[10px] text-gray-600">无布线数据</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 区域 E: 同工艺纵向结论 — 同设计同工艺下单一变量 (利用率/频率) 变化趋势 */}
      {verticalGroups.length > 0 && (
        <div className="bg-gray-900 border border-gray-700 rounded p-3">
          <h4 className="text-sm font-medium text-gray-300">📈 同工艺纵向结论</h4>
          <p className="text-[10px] text-gray-600 mt-0.5 mb-2">
            同一设计在同一工艺下, 仅一个变量取值不同时的趋势 (与上方横向总表互补)
          </p>
          <div className="space-y-3">
            {verticalGroups.map((g, gi) => {
              const gDesign = g.rows[0].combo?.design || results?.experiment?.design || '-'
              const gPdk = g.rows[0].combo?.PDK || '-'
              const fixedKeys: [string, any][] = Object.entries(g.rows[0].combo || {})
                .filter(([k]) => k !== g.axis && k !== 'design' && k !== 'PDK')
              const cols = groupMetricCols(g)
              const first = g.rows[0], last = g.rows[g.rows.length - 1]
              const fNum = (v: any) => typeof v === 'number' ? v.toFixed(3) : '-'
              const deltas = cols.map(mc => {
                const a = first[mc.key], b = last[mc.key]
                if (typeof a !== 'number' || typeof b !== 'number') return null
                const d = b - a
                const sym = Math.abs(d) < 1e-9 ? '—' : `${d > 0 ? '+' : ''}${d.toFixed(3)}`
                return `${mc.label}: ${fNum(a)}→${fNum(b)} (${sym})`
              }).filter(Boolean)
              return (
                <div key={gi} className="border border-gray-800 rounded p-2">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className="text-xs font-medium text-blue-300 font-mono">{gPdk}</span>
                    <span className="text-xs text-gray-300 font-mono">{gDesign}</span>
                    {fixedKeys.map(([k, v]) => (
                      <span key={k} className="text-[10px] text-gray-500 bg-gray-800/60 rounded px-1.5 py-0.5">{k}={v}</span>
                    ))}
                    <span className="text-[10px] text-yellow-500 bg-yellow-900/20 rounded px-1.5 py-0.5">{g.axis} 为变量</span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-[11px]">
                      <thead>
                        <tr className="text-gray-500 border-b border-gray-800">
                          <th className="text-left py-1 px-2">{g.axis}</th>
                          {cols.map(mc => <th key={mc.key} className="text-left py-1 px-2">{mc.label}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {g.rows.map((r, ri) => (
                          <tr key={ri} className="border-b border-gray-800/50">
                            <td className="py-1 px-2 text-gray-200 font-mono">{String(r.combo?.[g.axis] ?? '-')}</td>
                            {cols.map(mc => {
                              const v = r[mc.key]
                              return (
                                <td key={mc.key} className={`py-1 px-2 ${cellCls(mc.key, v)}`}>
                                  {typeof v === 'number' ? v.toFixed(3) : '-'}
                                </td>
                              )
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {deltas.length > 0 && (
                    <div className="text-[10px] text-gray-400 mt-1">
                      结论: {g.axis} {String(first.combo?.[g.axis])} → {String(last.combo?.[g.axis])}: {deltas.join(' · ')}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {results?.error && (
        <div className="bg-red-900/20 border border-red-800 rounded p-3 text-xs text-red-400">{results.error}</div>
      )}
    </div>
  )
}

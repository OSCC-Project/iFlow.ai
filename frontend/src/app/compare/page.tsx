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

  const runExperiment = async () => {
    setRunning(true); setResults(null); setProgress({})
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

  const exportCSV = () => {
    const cols = results?.summary?.columns || []
    const lines = [cols.join(',')]
    rows.forEach((r: any) => lines.push(cols.map((c: string) => {
      const v = r[c]
      return v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v).replace(/"/g, "'") : String(v)
    }).join(',')))
    const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob); a.download = `compare_${design}_${Date.now()}.csv`; a.click()
  }

  const metricOptions = [
    { key: 'area', label: '面积 (μm²)', color: '#34d399' },
    { key: 'wns', label: 'WNS (ns)', color: '#60a5fa' },
    { key: 'drc', label: 'DRC 违规数', color: '#f87171' },
  ] as const

  return (
    <div className="max-w-6xl mx-auto space-y-4">
      <h2 className="text-lg font-bold text-blue-400">📊 对比实验</h2>

      {/* Config */}
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-3">
          <div>
            <label className="text-xs text-gray-500 block mb-1">设计</label>
            <select value={design} onChange={e => setDesign(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm w-full">
              {['gcd', 'aes_cipher_top', 'uart', ...Object.keys(designUploads)].map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>

          {/* 用户自主添加: 上传设计 RTL / 上传工艺 liberty (PPA-only) */}
          <div className="space-y-2 bg-gray-800/30 rounded p-2">
            <div>
              <label className="text-xs text-gray-500 block mb-1">⬆ 上传设计 (.v, 可多选) — 加入上方下拉框</label>
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
                <label className="text-xs text-gray-500 block mb-1">值 (逗号分隔)</label>
                <input value={v.values} onChange={e => {
                  setVariables(variables.map(x => x.id===v.id ? {...x, values: e.target.value} : x))
                }} className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs w-full"
                  placeholder="sky130, nangate45" />
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
            <div>设计: <span className="text-gray-200">{design}</span></div>
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

      {/* 区域 A: 总表 + 导出 */}
      {results && !results.error && (
        <div className="bg-gray-900 border border-gray-700 rounded p-3">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-medium text-gray-300">对比结果 ({rows.length} 组)</h4>
            <button onClick={exportCSV} className="text-blue-400 text-[11px] hover:underline">导出 CSV</button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-400 border-b border-gray-800">
                  {results.summary?.columns?.map((c: string) => (
                    <th key={c} onClick={() => sortRows(c)}
                      className="text-left py-1.5 px-2 whitespace-nowrap cursor-pointer hover:text-white select-none">
                      {c}{sortCol === c ? (sortDir === 1 ? ' ↑' : ' ↓') : ''}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sortedRows.map((row: any, i: number) => (
                  <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    {results.summary.columns.map((c: string) => (
                      <td key={c} className={`py-1 px-2 whitespace-nowrap ${cellCls(c, row[c])}`}>
                        {typeof row[c] === 'object' ? JSON.stringify(row[c]) : String(row[c] ?? '-')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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

      {results?.error && (
        <div className="bg-red-900/20 border border-red-800 rounded p-3 text-xs text-red-400">{results.error}</div>
      )}
    </div>
  )
}

'use client'
import { useState } from 'react'

const API = 'http://localhost:8000'

interface VarConfig { id: string; type: string; values: string }

const VAR_TYPES = ['工艺 (PDK)', '利用率', '目标频率 (MHz)', '设计版本', '工具参数']

export default function Compare() {
  const [design, setDesign] = useState('gcd')
  const [variables, setVariables] = useState<VarConfig[]>([
    { id: '1', type: '工艺 (PDK)', values: 'sky130, nangate45' },
    { id: '2', type: '利用率', values: '35%, 30%' },
  ])
  const [running, setRunning] = useState(false)
  const [results, setResults] = useState<any>(null)

  const addVar = () => setVariables([...variables, { id: Date.now().toString(), type: '工艺 (PDK)', values: '' }])
  const removeVar = (id: string) => setVariables(variables.filter(v => v.id !== id))
  const comboCount = variables.reduce((n, v) => n * (v.values.split(',').filter(Boolean).length || 1), 1)

  const runExperiment = async () => {
    setRunning(true)
    try {
      const vars: any = {}
      variables.forEach(v => { if (v.values.trim()) vars[v.type] = v.values })
      const r1 = await fetch(`${API}/api/experiment/create`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ design, variables: vars })
      })
      const exp = await r1.json()
      const r2 = await fetch(`${API}/api/experiment/${exp.id}/run`, { method: 'POST' })
      const data = await r2.json()
      setResults(data)
    } catch (e: any) { setResults({ error: String(e.message) }) }
    setRunning(false)
  }

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <h2 className="text-lg font-bold text-blue-400">📊 对比实验</h2>

      {/* Config */}
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-3">
          <div>
            <label className="text-xs text-gray-500 block mb-1">设计</label>
            <select value={design} onChange={e => setDesign(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm w-full">
              {['gcd', 'aes', 'uart', 'picorv32'].map(d => <option key={d} value={d}>{d}</option>)}
            </select>
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
                  placeholder="sky130, nangate45, asap7" />
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
            className="mt-3 bg-green-600 px-4 py-1.5 rounded text-sm w-full">
            {running ? '运行中...' : '▶ 开始实验'}
          </button>
        </div>
      </div>

      {/* Results */}
      {results && !results.error && (
        <div className="bg-gray-900 border border-gray-700 rounded p-3">
          <h4 className="text-sm font-medium mb-2 text-gray-300">对比结果 ({results.summary?.rows?.length || 0} 组)</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-400 border-b border-gray-800">
                  {results.summary?.columns?.map((c: string) => (
                    <th key={c} className="text-left py-1.5 px-2">{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {results.summary?.rows?.map((row: any, i: number) => (
                  <tr key={i} className="border-b border-gray-800/50">
                    {results.summary.columns.map((c: string) => (
                      <td key={c} className="py-1 px-2 text-gray-400">{typeof row[c] === 'object' ? JSON.stringify(row[c]) : String(row[c] ?? '-')}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {results?.error && (
        <div className="bg-red-900/20 border border-red-800 rounded p-3 text-xs text-red-400">{results.error}</div>
      )}
    </div>
  )
}

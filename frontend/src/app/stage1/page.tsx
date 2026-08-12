'use client'
import { useState, useEffect } from 'react'
import dynamic from 'next/dynamic'
import { autosave } from '@/lib/autosave'
const Editor = dynamic(() => import('@monaco-editor/react'), { ssr: false })

const API = 'http://localhost:8000'
type SimResult = { step: string; status: string; duration: number; success?: boolean; violations?: number; errors?: number; assertions_ok?: boolean; stdout?: string; error?: string; reason?: string }

export default function Stage1() {
  const [mode, setMode] = useState<'ai'|'manual'|'upload'>('ai')
  const [question, setQuestion] = useState('设计一个带异步复位和使能的4位加法计数器')
  const [code, setCode] = useState('')
  const [tbCode, setTbCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [simResults, setSimResults] = useState<SimResult[]>([])
  const [hydrated, setHydrated] = useState(false)

  useEffect(() => {
    setCode(localStorage.getItem('s1_code') || '')
    setTbCode(localStorage.getItem('s1_tb') || '')
    setQuestion(localStorage.getItem('s1_question') || '设计一个带异步复位和使能的4位加法计数器')
    try { setSimResults(JSON.parse(localStorage.getItem('s1_results') || '[]')) } catch {}
    setHydrated(true)
  }, [])

  const save = (k: string, v: string) => { localStorage.setItem(k, v) }

  const runFlow = async () => {
    setLoading(true); setSimResults([])
    try {
      const r1 = await fetch(`${API}/api/flow/compose`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scene: 'experience', design: 'my_design' }) })
      if (!r1.ok) throw new Error(`compose: ${r1.status}`)
      const flow = await r1.json()
      const r2 = await fetch(`${API}/api/flow/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ flow_id: flow.flow_id, rtl_code: code }) })
      if (!r2.ok) throw new Error(`run: ${r2.status}`)
      const run = await r2.json()
      if (mode === 'ai') setSimResults(prev => [...prev.filter(s => s.step.includes('ChipMATE')), ...(run.results || [])])
      else setSimResults(run.results || [])
      save('s1_results', JSON.stringify(run.results || []))
    } catch (e: any) {
      setSimResults([{ step: 'error', status: 'failed', duration: 0, error: e.message === 'Failed to fetch' ? '无法连接后端' : String(e.message) }])
    }
    setLoading(false)
  }

  const generateRTL = async () => {
    setLoading(true)
    setSimResults([])  // 清空上一次的编译/验证结果
    try {
      const r = await fetch(`${API}/api/rtl/generate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }) })
      const d = await r.json()
      const v = d.verilog || `生成失败: ${d.error || '未知'}`
      setCode(v); save('s1_code', v)
      setTbCode(''); save('s1_tb', '')
      autosave('rtl.v', v)
    } catch { setCode('API 连接失败') }
    setLoading(false)
  }

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <h2 className="text-lg font-bold text-blue-400">📐 阶段 1: RTL 设计与生成</h2>

      <div className="flex gap-1 bg-gray-900 rounded p-1 w-fit">
        {(['ai','manual','upload'] as const).map(m => (
          <button key={m} onClick={() => setMode(m)} className={`px-4 py-1.5 rounded text-xs ${mode===m ? 'bg-blue-600' : 'hover:bg-gray-800 text-gray-400'}`}>
            {{ai:'🤖 AI 生成', manual:'✍ 手写代码', upload:'📎 上传文件'}[m]}
          </button>
        ))}
      </div>

      {mode === 'ai' && (
        <div className="flex gap-2">
          <input value={question} onChange={e => { setQuestion(e.target.value); save('s1_question', e.target.value) }}
            className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm" placeholder="描述你的设计需求..." />
          <button onClick={generateRTL} disabled={loading} className="bg-blue-600 px-4 py-2 rounded text-sm whitespace-nowrap">{loading ? '...' : '🚀 生成'}</button>
        </div>
      )}

      {mode === 'upload' && (
        <input type="file" accept=".v,.sv" onChange={e => { const f = e.target.files?.[0]; if (f) { const r = new FileReader(); r.onload = () => { setCode(r.result as string); save('s1_code', r.result as string) }; r.readAsText(f) } }}
          className="text-xs text-gray-400 file:bg-gray-700 file:border-0 file:rounded file:px-3 file:py-1" />
      )}

      <div className="border border-gray-700 rounded overflow-hidden" style={{ height: 'min(70vh, 600px)', resize: 'vertical' } as any}>
        <Editor
          language="verilog"
          value={code}
          onChange={v => { if (mode !== 'ai' && v) { setCode(v); save('s1_code', v) } }}
          theme="vs-dark"
          options={{ fontSize: 12, minimap: { enabled: false }, readOnly: mode === 'ai', scrollBeyondLastLine: false, lineNumbers: 'on', wordWrap: 'on' }}
          height="100%"
        />
      </div>

      <button onClick={runFlow} disabled={!hydrated || loading || !code}
        className="bg-green-600 hover:bg-green-700 disabled:bg-gray-700 px-4 py-2 rounded text-sm">
        {!hydrated ? '加载中...' : loading ? '运行中...' : '▶ 编译 & 仿真'}
      </button>

      {/* ===== 编译检查结果 ===== */}
      <div className="bg-gray-900 border border-gray-700 rounded p-3">
        <h4 className="text-sm font-medium text-gray-300 mb-2">📋 编译检查</h4>
        {simResults.filter(s => s.step !== 'error').length > 0 ? (
          <div className="grid grid-cols-3 gap-2 text-xs">
            {simResults.filter(s => s.step !== 'error').map((s, i) => (
              <div key={i} className={`rounded p-2 ${s.status==='done' && s.success!==false ? 'bg-green-900/20 border border-green-800' : s.status==='failed' ? 'bg-red-900/20 border border-red-800' : s.status==='skipped' ? 'bg-gray-800/50 border border-gray-700' : 'bg-gray-800/50'}`}>
                <div className="flex items-center gap-1.5 mb-1"><span>{s.status==='done' ? (s.success===false ? '❌' : '✅') : s.status==='failed' ? '❌' : s.status==='skipped' ? '⏭️' : '⏳'}</span><span className="text-gray-300">{s.step}</span></div>
                <div className="text-gray-500">{s.duration}s</div>
                {s.status==='done' && s.step==='verible_lint' && <div className="text-gray-400 mt-0.5">违例: {s.violations??0}</div>}
                {s.status==='done' && s.step==='verilator_lint' && <div className="text-gray-400 mt-0.5">Error: {s.errors??0}</div>}
                {s.status==='done' && s.step==='icarus_sim' && <div className={s.success ? 'text-green-400 mt-0.5' : 'text-red-400 mt-0.5'}>{s.success ? '通过' : '失败'}</div>}
                {(s as any).reason && <div className="text-gray-500 mt-0.5 text-[10px]">{(s as any).reason}</div>}
                {s.status==='failed' && <div className="text-red-400 mt-0.5 text-[10px] break-all">{(s as any).error || '未知错误'}</div>}
                {(s as any).error && s.status!=='failed' && <div className="text-red-400 mt-0.5 text-[10px] break-all">{(s as any).error}</div>}
              </div>
            ))}
          </div>
        ) : simResults.some(s => s.step === 'error') ? (
          <p className="text-xs text-red-400">{simResults.find(s => s.step === 'error')?.error}</p>
        ) : (
          <p className="text-xs text-gray-500">还没有运行结果，点上方"编译 & 仿真"开始</p>
        )}
      </div>


      {/* ===== 通关条件 ===== */}
      <div className="grid grid-cols-3 gap-2 text-xs">
        {[{m:'🥉',l:'编译无 Error'},{m:'🥈',l:'仿真波形正确'},{m:'🥇',l:'Lint + Style Check 通过'}].map((b,i) => (
          <div key={i} className="bg-gray-800/50 rounded p-2 text-center"><span className="text-lg">{b.m}</span><div className="text-gray-400">{b.l}</div></div>
        ))}
      </div>
    </div>
  )
}

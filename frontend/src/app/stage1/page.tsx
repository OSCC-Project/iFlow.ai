'use client'
import { useState, useEffect } from 'react'
import dynamic from 'next/dynamic'
import { autosave } from '@/lib/autosave'
import { awardStage1 } from '@/lib/badges'
import { addOp } from '@/lib/oplog'
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
  const [cmDetail, setCmDetail] = useState<any>(null)
  const [pyModel, setPyModel] = useState('')
  const [topModule, setTopModule] = useState('')
  const [hydrated, setHydrated] = useState(false)

  useEffect(() => {
    setCode(localStorage.getItem('s1_code') || '')
    setTbCode(localStorage.getItem('s1_tb') || '')
    setQuestion(localStorage.getItem('s1_question') || '设计一个带异步复位和使能的4位加法计数器')
    try { setSimResults(JSON.parse(localStorage.getItem('s1_results') || '[]')) } catch {}
    // ChipMATE 交叉验证详情持久化 (刷新/重进页面不丢失)
    try { setCmDetail(JSON.parse(localStorage.getItem('s1_cm_detail') || 'null')) } catch {}
    setPyModel(localStorage.getItem('s1_py_model') || '')
    setHydrated(true)
  }, [])

  const save = (k: string, v: string) => { localStorage.setItem(k, v) }

  const runFlow = async () => {
    setLoading(true); setSimResults([])
    // P1-6: WebSocket 实时步骤推送 (run_id 前端生成)
    const myRunId = 'run_' + Math.random().toString(36).slice(2, 10)
    let ws: WebSocket | null = null
    try {
      ws = new WebSocket(`ws://localhost:8000/ws/${myRunId}`)
      ws.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data)
          if (ev.type === 'step_start') {
            setSimResults(prev => {
              if (prev.some(s => s.step === ev.step)) return prev.map(s => s.step===ev.step ? {...s, status:'running'} : s)
              return [...prev, { step: ev.step, status: 'running', duration: 0 }]
            })
          } else if (ev.type === 'step_done') {
            setSimResults(prev => prev.map(s => s.step === ev.step ? { ...s,
              status: ev.status === 'skipped' ? 'skipped' : (ev.status === 'failed' || ev.success === false) ? 'failed' : 'done',
              success: ev.success, duration: ev.duration } : s))
          }
        } catch {}
      }
    } catch {}
    try {
      const r1 = await fetch(`${API}/api/flow/compose`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scene: 'experience', design: 'my_design', rtl_code: code }) })
      if (!r1.ok) throw new Error(`compose: ${r1.status}`)
      const flow = await r1.json()
      // 参考模型只在其绑定的代码快照一致时复用 (手动/上传模式的代码与 AI 模型无关)
      const pyModelForRun = code === localStorage.getItem('s1_py_code') ? pyModel : ''
      const r2 = await fetch(`${API}/api/flow/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ flow_id: flow.flow_id, rtl_code: code, params: { run_id: myRunId, py_model: pyModelForRun } }) })
      if (!r2.ok) throw new Error(`run: ${r2.status}`)
      const run = await r2.json()
      if (mode === 'ai') setSimResults(prev => [...prev.filter(s => s.step.includes('ChipMATE')), ...(run.results || [])])
      else setSimResults(run.results || [])
      save('s1_results', JSON.stringify(run.results || []))
      awardStage1(run.results || [])  // 通关徽章: 铜=编译 / 银=波形匹配 / 金=100%+Lint
      const fails = (run.results || []).filter((s: any) => s.status === 'failed' || (s.status === 'done' && s.success === false)).length
      addOp(1, `${mode === 'ai' ? 'AI 生成' : mode === 'upload' ? '上传' : '手写'} RTL 编译+仿真 ${fails === 0 ? '✅ 通过' : `❌ ${fails} 项失败`}`, fails === 0)
      // 自动激励仿真的交叉验证详情同样持久化 (保持 ChipMATE 面板长期可见)
      const sim = (run.results || []).find((s: any) => s.step === 'icarus_sim')
      if (sim?.detail) {
        setCmDetail(sim.detail)
        save('s1_cm_detail', JSON.stringify(sim.detail))
        if (sim.py_model) {
          setPyModel(sim.py_model)
          save('s1_py_model', sim.py_model)
          save('s1_py_code', code)  // 现场生成的模型绑定当前代码
        }
      }
    } catch (e: any) {
      setSimResults([{ step: 'error', status: 'failed', duration: 0, error: e.message === 'Failed to fetch' ? '无法连接后端' : String(e.message) }])
    }
    ws?.close()
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
      // ChipMATE 交叉验证详情 + Python 参考模型 (持久化到 localStorage)
      // 模型与其生成时的代码快照 (s1_py_code) 绑定 — 代码变了模型才失效
      setCmDetail(d.detail || null)
      setPyModel(d.py_model || '')
      if (d.detail) save('s1_cm_detail', JSON.stringify(d.detail))
      if (d.py_model) { save('s1_py_model', d.py_model); save('s1_py_code', v) }
      else { localStorage.removeItem('s1_py_model'); localStorage.removeItem('s1_py_code') }
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
        <div className="space-y-1">
          <input type="file" accept=".v,.sv" onChange={e => { const f = e.target.files?.[0]; if (f) { const r = new FileReader(); r.onload = () => {
            // P2-1: 自动识别顶层 module (方案 RTL-003: filelist/顶层自动识别)
            const text = r.result as string
            setCode(text); save('s1_code', text)
            const m = text.match(/module\s+(\w+)/)
            setTopModule(m ? m[1] : '')
            const mods = text.match(/module\s+(\w+)/g) || []
            if (mods.length > 1) console.log('检测到多模块:', mods.join(', '))
          }; r.readAsText(f) } }}
            className="text-xs text-gray-400 file:bg-gray-700 file:border-0 file:rounded file:px-3 file:py-1" />
          {topModule && <div className="text-[11px] text-gray-500">🔍 检测到顶层模块: <span className="text-blue-400 font-mono">{topModule}</span></div>}
        </div>
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


      {/* ===== ChipMATE 交叉验证详情 ===== */}
      {cmDetail && (
        <div className="bg-gray-900 border border-gray-700 rounded p-3 space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-medium text-gray-300">🔬 ChipMATE 交叉验证</h4>
            <span className={`text-xs font-bold ${cmDetail.mismatched === 0 ? 'text-green-400' : 'text-red-400'}`}>
              {cmDetail.mismatched === 0 ? '✅ 全部匹配' : `❌ ${cmDetail.mismatched}/${cmDetail.total_checks} 不匹配`}
            </span>
          </div>
          <div className="text-[10px] text-gray-500">
            {cmDetail.num_tests} 组随机输入 | 输出: {cmDetail.outputs?.join(', ')} | 匹配率 {(cmDetail.match_rate*100).toFixed(0)}%
          </div>

          {/* Python 参考模型 */}
          {pyModel && (
            <div>
              <h5 className="text-xs text-gray-400 mb-1">🐍 Python 参考模型 (独立实现, 未参考 Verilog)</h5>
              <pre className="bg-gray-950 rounded p-2 font-mono text-[10px] text-green-300 overflow-x-auto max-h-40">{pyModel}</pre>
            </div>
          )}

          {/* 逐测试对比表 */}
          {cmDetail.sv_results && cmDetail.py_results && (
            <div>
              <h5 className="text-xs text-gray-400 mb-1">📊 逐测试对比</h5>
              <div className="overflow-x-auto">
                <table className="w-full text-[10px] border-collapse">
                  <thead>
                    <tr className="text-gray-400">
                      <th className="border border-gray-700 px-2 py-1 text-left">#</th>
                      <th className="border border-gray-700 px-2 py-1 text-left">输入</th>
                      {cmDetail.outputs.map((o:string) => (
                        <th key={o} className="border border-gray-700 px-2 py-1 text-left" colSpan={2}>{o}</th>
                      ))}
                      <th className="border border-gray-700 px-2 py-1 text-left">结果</th>
                    </tr>
                    <tr className="text-gray-500">
                      <th className="border border-gray-700 px-2 py-0.5"></th>
                      <th className="border border-gray-700 px-2 py-0.5"></th>
                      {cmDetail.outputs.map((o:string) => (
                        <th key={o} className="border border-gray-700 px-2 py-0.5 text-[8px] font-normal" colSpan={2}>Verilog / Python</th>
                      ))}
                      <th className="border border-gray-700 px-2 py-0.5"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {cmDetail.sv_results.map((sv:any, i:number) => {
                      const py = cmDetail.py_results[i] || {}
                      const ok = cmDetail.outputs.every((o:string) => sv[o] === py[o])
                      return (
                        <tr key={i} className={ok ? '' : 'bg-red-900/20'}>
                          <td className="border border-gray-700 px-2 py-0.5 text-gray-500">{i}</td>
                          <td className="border border-gray-700 px-2 py-0.5 text-gray-500 font-mono">
                            {JSON.stringify(cmDetail.stimuli?.[i] || {})}
                          </td>
                          {cmDetail.outputs.map((o:string) => (
                            <td key={o} className="border border-gray-700 px-2 py-0.5 text-center" colSpan={2}>
                              <span className={sv[o]===py[o] ? 'text-green-400' : 'text-red-400'}>
                                {sv[o]} / {py[o]}
                              </span>
                            </td>
                          ))}
                          <td className="border border-gray-700 px-2 py-0.5 text-center">{ok ? '✅' : '❌'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Mismatch 详情 */}
          {cmDetail.mismatches && cmDetail.mismatches.length > 0 && (
            <div>
              <h5 className="text-xs text-red-400 mb-1">⚠️ 不匹配详情 (前 {cmDetail.mismatches.length} 个)</h5>
              {cmDetail.mismatches.map((mm:any, i:number) => (
                <div key={i} className="text-[10px] text-red-300 bg-red-900/20 rounded p-1.5 mb-1 font-mono">
                  测试#{mm.test} 信号 {mm.signal}: Verilog={mm.verilog} Python={mm.python} 输入={JSON.stringify(mm.inputs)}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ===== 通关条件 (自动判定) ===== */}
      {(() => {
        const verilator = simResults.find((s: any) => s.step === 'verilator_lint')
        const icarus = simResults.find((s: any) => s.step === 'icarus_sim')
        const verible = simResults.find((s: any) => s.step === 'verible_lint')
        // 铜: Verilator 编译 0 Error
        const bronze = verilator?.status === 'done' && verilator?.errors === 0
        // 银: Icarus 仿真通过
        const silver = icarus?.status === 'done' && icarus?.success === true
        // 金: Verible 0 违例 (Lint + Style Check)
        const gold = verible?.status === 'done' && verible?.violations === 0
        return (
          <div className="grid grid-cols-3 gap-2 text-xs">
            {[
              {m:'🥉', l:'编译无 Error', ok: bronze, detail: verilator ? `Error: ${verilator.errors}` : '未运行'},
              {m:'🥈', l:'仿真波形正确', ok: silver, detail: icarus ? (icarus.success ? '通过' : '失败') : '未运行'},
              {m:'🥇', l:'Lint + Style Check 通过', ok: gold, detail: verible ? `违例: ${verible.violations}` : '未运行'},
            ].map((b,i) => (
              <div key={i} className={`rounded p-2 text-center border ${b.ok ? 'bg-green-900/30 border-green-700' : 'bg-gray-800/50 border-gray-700'}`}>
                <span className={`text-lg ${b.ok ? '' : 'grayscale opacity-40'}`}>{b.m}</span>
                <div className={`${b.ok ? 'text-green-400' : 'text-gray-400'}`}>{b.l}</div>
                <div className={`text-[9px] mt-0.5 ${b.ok ? 'text-green-600' : 'text-gray-600'}`}>{b.detail}</div>
              </div>
            ))}
          </div>
        )
      })()}
    </div>
  )
}

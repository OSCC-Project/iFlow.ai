'use client'
import { useState, useEffect, useRef } from 'react'
import Markdown from '@/components/Markdown'

const API = 'http://localhost:8000'
type Step = { name: string; status: 'pending'|'running'|'done'|'failed'; duration?: number; reason?: string; error?: string }
type Msg = { role: 'user'|'agent'; text: string }

export default function Stage3() {
  const [code, setCode] = useState('')
  const [chat, setChat] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [steps, setSteps] = useState<Step[]>([])
  const [skipped, setSkipped] = useState<string[]>([])
  const [intensity, setIntensity] = useState<any>({})
  const [flowId, setFlowId] = useState('')
  const [running, setRunning] = useState(false)
  const [phase, setPhase] = useState<'chat'|'composed'|'running'|'done'>('chat')
  const [stage1Results, setStage1Results] = useState<any>(null)
  const [stage2Results, setStage2Results] = useState<any>(null)
  const chatEnd = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setCode(localStorage.getItem('s1_code') || '')
    try { setStage1Results(JSON.parse(localStorage.getItem('s1_results')||'null')) } catch {}
    try { setStage2Results(JSON.parse(localStorage.getItem('s2_results')||'null')) } catch {}
    try { setChat(JSON.parse(localStorage.getItem('s3_chat')||'[]')) } catch {}
    try { const ss=JSON.parse(localStorage.getItem('s3_steps')||'[]'); if(ss.length){setSteps(ss);setPhase(localStorage.getItem('s3_phase') as any||'chat')} } catch {}
    try { setSkipped(JSON.parse(localStorage.getItem('s3_skipped')||'[]')) } catch {}
    if (!localStorage.getItem('s3_chat')) {
      setChat([{ role:'agent', text:'你好！描述你的需求，我帮你拼装物理实现流程。' }])
    }
  }, [])

  useEffect(() => { chatEnd.current?.scrollIntoView({behavior:'smooth'}) }, [chat])

  // 精确检查阶段状态 — 关键步骤必须真正通过
  const checkStage = (results: any, required: string[]) => {
    if (!results || results.length === 0) return 'none'  // 未运行
    for (const step of required) {
      const r = results.find((s: any) => s.step === step)
      if (!r) return 'incomplete'  // 缺少关键步骤
      if (r.status === 'failed') return 'failed'
      if (r.status === 'skipped') return 'incomplete'
      if (r.status === 'done' && r.success === false) return 'failed'
      if (r.status !== 'done') return 'running'
    }
    return 'pass'
  }

  const s1status = checkStage(stage1Results, ['verible_lint', 'verilator_lint', 'icarus_sim'])
  const s2status = checkStage(stage2Results, ['icarus_sim'])

  const statusLabel = (s: string) => {
    switch(s) {
      case 'pass': return { icon: '✅', text: '通过', color: 'text-green-400', bg: 'bg-green-900/20 border-green-800' }
      case 'failed': return { icon: '❌', text: '未通过', color: 'text-red-400', bg: 'bg-red-900/20 border-red-800' }
      case 'incomplete': return { icon: '⚠️', text: '不完整', color: 'text-yellow-400', bg: 'bg-yellow-900/20 border-yellow-800' }
      case 'running': return { icon: '🔄', text: '运行中', color: 'text-blue-400', bg: 'bg-blue-900/20 border-blue-800' }
      default: return { icon: '—', text: '未运行', color: 'text-gray-600', bg: 'bg-gray-800/50' }
    }
  }

  const s1 = statusLabel(s1status), s2 = statusLabel(s2status)

  const send = async () => {
    if (!input.trim()) return
    const msg = input.trim()
    setChat((prev:any)=>{const nx=[...prev,{role:'user',text:msg}];localStorage.setItem('s3_chat',JSON.stringify(nx));return nx})
    setInput('')
    try {
      const ctx = [
        `RTL代码: ${code.slice(0,1500)}`,
        stage1Results ? `阶段1结果: ${JSON.stringify(stage1Results).slice(0,500)}` : '',
        stage2Results ? `阶段2结果: ${JSON.stringify(stage2Results).slice(0,500)}` : '',
      ].filter(Boolean).join('\n---\n')
      const r = await fetch(`${API}/api/chat`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message:msg, session_id:'stage3', context:ctx}) })
      const d = await r.json()
      setChat((prev:any)=>{const nx=[...prev,{role:'agent',text:d.reply}];localStorage.setItem('s3_chat',JSON.stringify(nx));return nx})
      if (!d.flow) return
      setFlowId(d.flow.flow_id); setSteps(d.flow.steps.map((s:string)=>({name:s,status:'pending'})))
      setSkipped(d.flow.skipped||[]); setIntensity(d.flow.intensity||{}); setPhase('composed')
      localStorage.setItem('s3_steps',JSON.stringify(d.flow.steps.map((s:string)=>({name:s,status:'pending'}))))
      localStorage.setItem('s3_skipped',JSON.stringify(d.flow.skipped||[])); localStorage.setItem('s3_phase','composed')
    } catch { setChat(prev => [...prev, { role:'agent', text:'连接失败' }]) }
  }

  const execute = async () => {
    setRunning(true); setPhase('running')
    try {
      const r = await fetch(`${API}/api/flow/run`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({flow_id:flowId, rtl_code:code}) })
      const run = await r.json()
      const results: Step[] = (run.results||[]).map((r:any)=>({name:r.step, status:r.status==='failed'?'failed':'done', duration:r.duration, reason:r.reason, error:r.error}))
      for (let i=0; i<results.length; i++) { setSteps(prev=>prev.map((s,j)=>j===i?results[i]:s)); await new Promise(r=>setTimeout(r,80)) }
      setPhase('done')
      const ok = results.every(r=>r.status==='done')
      setChat(prev => [...prev, { role:'agent', text: ok ? `✅ 全部 ${results.length} 步完成。` : `⚠️ ${results.filter(r=>r.status==='done').length}/${results.length} 步完成` }])
    } catch(e:any) { setChat(prev => [...prev, { role:'agent', text:'执行失败: '+e.message }]) }
    setRunning(false)
  }

  const reset = () => { setPhase('chat'); setSteps([]); setSkipped([]) }

  return (
    <div className="flex gap-3 h-[calc(100vh-170px)]">
      {/* ===== 左栏: 前置状态 + RTL ===== */}
      <div className="w-[35%] flex flex-col gap-3 overflow-y-auto shrink-0">
        {/* 阶段1/2 状态 */}
        <div className="bg-gray-900 border border-gray-700 rounded p-3">
          <h4 className="text-xs font-medium text-gray-300 mb-2">📊 前置阶段</h4>
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            {[{label:'阶段1 RTL设计',s:s1,steps:'Verible+Verilator+Icarus'},{label:'阶段2 仿真验证',s:s2,steps:'Icarus 仿真'}].map((st,i)=>(
              <div key={i} className={`rounded p-2 ${st.s.bg}`}>
                <div className="text-gray-400">{st.label}</div>
                <div className={`font-medium ${st.s.color}`}>{st.s.icon} {st.s.text}</div>
                <div className="text-gray-600 text-[9px] mt-0.5">{st.steps}</div>
              </div>
            ))}
          </div>
        </div>

        {/* RTL */}
        <div className="bg-gray-900 border border-gray-700 rounded flex-1 min-h-0 flex flex-col">
          <div className="px-3 py-2 border-b border-gray-800"><h4 className="text-xs font-medium text-gray-300">📄 RTL ({code.length} 字符)</h4></div>
          <textarea value={code} readOnly className="flex-1 bg-transparent p-3 font-mono text-[11px] text-gray-400 focus:outline-none resize-none" />
        </div>
      </div>

      {/* ===== 中栏: Flow ===== */}
      <div className="flex-1 flex flex-col gap-3 overflow-y-auto min-w-0">
        {phase === 'chat' ? (
          <div className="flex-1 flex items-center justify-center text-xs text-gray-600">
            👈 右侧聊天框描述你的需求，Agent 自动拼装 Flow
          </div>
        ) : (
          <>
            <div className="bg-gray-900 border border-gray-700 rounded">
              <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
                <h4 className="text-xs font-medium text-gray-300">📋 Flow ({steps.length} 步)</h4>
                <div className="flex gap-1">
                  {phase==='composed' && <button onClick={execute} className="bg-green-600 px-3 py-0.5 rounded text-[11px]">▶ 执行</button>}
                  {running && <span className="text-blue-400 text-[11px] animate-pulse">执行中...</span>}
                  {phase==='done' && <span className="text-green-400 text-[11px]">✅ 完成</span>}
                  <button onClick={reset} className="text-gray-500 hover:text-white text-[11px]">重置</button>
                </div>
              </div>
              <div className="p-2 space-y-0.5 max-h-80 overflow-y-auto">
                {steps.map((s,i) => (
                  <div key={i} className={`flex items-center gap-1.5 text-[11px] py-0.5 px-1.5 rounded ${s.status==='running'?'bg-blue-900/20':s.status==='failed'?'bg-red-900/20':s.status==='done'?'bg-green-900/10':''}`}>
                    <span>{s.status==='done'?'✅':s.status==='running'?'🔄':s.status==='failed'?'❌':'⏳'}</span>
                    <span className="text-gray-400">{(i+1)}. {s.name}</span>
                    {s.duration !== undefined && <span className="text-gray-600 ml-auto">{s.duration}s</span>}
                    {s.error && <span className="text-red-400 ml-1 text-[10px] truncate max-w-[120px]">{s.error}</span>}
                  </div>
                ))}
              </div>
            </div>
            {(skipped.length>0||Object.keys(intensity).length>0) && (
              <div className="bg-gray-900/50 rounded p-2 text-[11px] text-gray-500">
                {skipped.length>0 && <div>🚫 跳过: {skipped.join(', ')}</div>}
                {Object.keys(intensity).length>0 && <div>⚡ {Object.entries(intensity).map(([k,v])=>`${k}=${v}`).join(', ')}</div>}
              </div>
            )}
          </>
        )}
      </div>

      {/* ===== 右栏: 聊天 ===== */}
      <div className="w-[30%] flex flex-col bg-gray-900 border border-gray-700 rounded shrink-0 min-w-0">
        <div className="px-3 py-2 border-b border-gray-800"><h4 className="text-xs font-medium text-gray-300">🤖 AI 助手</h4></div>
        <div className="flex-1 overflow-y-auto p-2 space-y-2">
          {chat.map((m,i) => (
            <div key={i} className={`flex gap-1 ${m.role==='user'?'justify-end':''}`}>
              {m.role==='agent' && <span className="text-blue-400 shrink-0 mt-0.5">🤖</span>}
              <div className={`max-w-[92%] rounded-lg px-2.5 py-1.5 text-[11px] leading-relaxed ${m.role==='user'?'bg-blue-600 text-white':'bg-gray-800 text-gray-200'}`}>
                {m.role==='agent' ? <Markdown text={m.text} /> : <span className='whitespace-pre-wrap'>{m.text}</span>}
              </div>
              {m.role==='user' && <span className="shrink-0 mt-0.5">👤</span>}
            </div>
          ))}
          <div ref={chatEnd} />
        </div>
        <div className="border-t border-gray-700 p-2 flex gap-1">
          <input value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>e.key==='Enter'&&send()}
            className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-[11px] focus:outline-none focus:border-blue-500"
            placeholder='描述需求...' />
          <button onClick={send} disabled={!input.trim()} className="bg-blue-600 px-2 py-1 rounded text-[11px] disabled:bg-gray-700">发送</button>
        </div>
      </div>
    </div>
  )
}

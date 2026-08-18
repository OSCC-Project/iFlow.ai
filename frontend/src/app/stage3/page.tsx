'use client'
import { useState, useEffect, useRef } from 'react'
import Markdown from '@/components/Markdown'
import dynamic from 'next/dynamic'
const ConvergenceChart = dynamic(() => import('@/components/ConvergenceChart'), { ssr: false })
import GdsPreview from '@/components/GdsPreview'
import { awardStage3 } from '@/lib/badges'
import { addOp } from '@/lib/oplog'
import { withToken } from '@/lib/authFetch'

const API = 'http://localhost:8000'
type Step = { name: string; status: 'pending'|'running'|'done'|'failed'|'skipped'; duration?: number; reason?: string; error?: string }
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
  const [convergence, setConvergence] = useState<any>(null)
  const [archive, setArchive] = useState<any>(null)
  const chatEnd = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setCode(localStorage.getItem('s1_code') || '')
    try { setStage1Results(JSON.parse(localStorage.getItem('s1_results')||'null')) } catch {}
    try { setStage2Results(JSON.parse(localStorage.getItem('s2_results')||'null')) } catch {}
    try { setConvergence(JSON.parse(localStorage.getItem('s3_convergence')||'null')) } catch {}
    try { setArchive(JSON.parse(localStorage.getItem('s3_archive')||'null')) } catch {}
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
  // 阶段2 判定必须真实: 功能仿真通过 + 形式验证通过; 没跑形式验证 = 不完整
  const s2status = (() => {
    const base = checkStage(stage2Results, ['icarus_sim'])
    if (base !== 'pass') return base
    const frPass = localStorage.getItem('s2_fr_pass')
    if (frPass === '0') return 'failed'
    if (frPass !== '1') return 'incomplete'
    return 'pass'
  })()

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

  const clearChat = async () => {
    // 清空本地 + 服务端会话, 旧对话不干扰新环境
    const greeting: Msg = { role:'agent', text:'你好！描述你的需求，我帮你拼装物理实现流程。' }
    setChat([greeting]); localStorage.setItem('s3_chat', JSON.stringify([greeting]))
    try { await fetch(`${API}/api/chat/clear`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({session_id:'stage3'}) }) } catch {}
  }

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
      // 兜底: 后端解析 ACTION 失败时, 根据关键字手动拼装
      if (!d.flow) {
        if (/综合|PPA|ppa|面积|频率|功耗/.test(msg)) { manualCompose('competition'); return }
        if (/版图|gds|GDS|物理实现/.test(msg)) { manualCompose('research'); return }
        if (/流片|签核|tapeout/.test(msg)) { manualCompose('tapeout'); return }
        return
      }
      setFlowId(d.flow.flow_id); setSteps(d.flow.steps.map((s:string)=>({name:s,status:'pending'})))
      setSkipped(d.flow.skipped||[]); setIntensity(d.flow.intensity||{}); setPhase('composed')
      localStorage.setItem('s3_steps',JSON.stringify(d.flow.steps.map((s:string)=>({name:s,status:'pending'}))))
      localStorage.setItem('s3_skipped',JSON.stringify(d.flow.skipped||[])); localStorage.setItem('s3_phase','composed')
    } catch { setChat(prev => [...prev, { role:'agent', text:'连接失败' }]) }
  }

  const execute = async () => {
    setRunning(true); setPhase('running')
    // WebSocket 订阅实时步骤更新
    const myRunId = 'run_' + Math.random().toString(36).slice(2, 10)
    let ws: WebSocket | null = null
    try {
      ws = new WebSocket(`ws://localhost:8000/ws/${myRunId}`)
      ws.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data)
          if (ev.type === 'step_start') {
            setSteps(prev => prev.map(s => s.name === ev.step ? {...s, status:'running'} : s))
          } else if (ev.type === 'step_done') {
            // P1-2: 区分 skipped (⏭️) 与 done (✅)
            const st: Step['status'] = ev.status === 'skipped' ? 'skipped'
              : ev.status === 'failed' || ev.success === false ? 'failed' : 'done'
            setSteps(prev => prev.map(s => s.name === ev.step ? {...s, status: st, duration: ev.duration} : s))
          }
        } catch {}
      }
    } catch {}
    try {
      const r = await fetch(`${API}/api/flow/run`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({flow_id:flowId, rtl_code:code, params:{run_id:myRunId}}) })
      const run = await r.json()
      const results: Step[] = (run.results||[]).map((r:any)=>({name:r.step,
        status: r.status==='failed' || (r.status==='done' && r.success===false) ? 'failed'
              : r.status==='skipped' ? 'skipped' : 'done',
        duration:r.duration, reason:r.reason, error:r.error}))
      for (let i=0; i<results.length; i++) { setSteps(prev=>prev.map((s,j)=>j===i?results[i]:s)); await new Promise(r=>setTimeout(r,80)) }
      // 找 GDS 文件并预览 (路径持久化, 组件自动加载)
      const gdsStep = run.results?.find((r:any)=>r.step==='gds_export' && r.gds_path)
      if (gdsStep) { setGdsPath(gdsStep.gds_path); localStorage.setItem('s3_gds_path', gdsStep.gds_path) }
      setPhase('done')
      // 活动 2: 收敛循环结果 (诊断→回溯→重跑, 含止损判定)
      if (run.convergence?.rounds?.length) {
        setConvergence(run.convergence)
        localStorage.setItem('s3_convergence', JSON.stringify(run.convergence))
        const c = run.convergence
        const cLabel = c.status==='converged' ? '✅ 收敛完成'
          : c.status==='stop_loss' ? '🛑 止损结束' : '⏱ 达到轮数上限'
        addOp(3, `收敛循环 ${c.rounds.length} 轮 ${cLabel}`, c.status==='converged')
        setChat(prev => [...prev, { role:'agent',
          text: `🔁 收敛循环: ${c.rounds.length} 轮, ${cLabel}` }])
      } else {
        localStorage.removeItem('s3_convergence'); setConvergence(null)
      }
      // 活动 3: 归档交付 (方案 6.3.3)
      if (run.archive?.report_path) {
        setArchive(run.archive)
        localStorage.setItem('s3_archive', JSON.stringify(run.archive))
        addOp(3, `归档交付: ${run.archive.title} (${run.archive.status==='delivered'?'✅ 可交付':'⚠️ 部分完成'})`, run.archive.status==='delivered')
      } else {
        localStorage.removeItem('s3_archive'); setArchive(null)
      }
      awardStage3(run.results || [])  // 通关徽章: 铜=PPA / 银=GDS / 金=时序+DRC clean
      const doneN = results.filter(r=>r.status==='done').length
      const skipN = results.filter(r=>r.status==='skipped').length
      const failN = results.filter(r=>r.status==='failed').length
      const ok = failN === 0
      addOp(3, `物理实现 ${ok ? '✅ 完成' : `⚠️ ${failN} 步失败`}`, ok)
      setChat(prev => [...prev, { role:'agent', text: ok ? `✅ 全部 ${results.length} 步完成${skipN?` (${skipN} 步因依赖跳过)`:' (无跳过)'}。` : `⚠️ ${doneN} 步完成${skipN?`, ${skipN} 步跳过`:''}, ${failN} 步失败` }])
    } catch(e:any) { setChat(prev => [...prev, { role:'agent', text:'执行失败: '+e.message }]) }
    setRunning(false)
  }

  const reset = () => { setPhase('chat'); setSteps([]); setSkipped([]); setConvergence(null); setArchive(null); localStorage.removeItem('s3_convergence'); localStorage.removeItem('s3_archive') }

  // 手动场景兜底: 不依赖 LLM ACTION 解析
  const manualCompose = async (scene: string) => {
    try {
      const r = await fetch(`${API}/api/flow/compose`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({scene, design:'my_design', rtl_code: code}) })
      const f = await r.json()
      setFlowId(f.flow_id); setSteps(f.steps.map((s:string)=>({name:s,status:'pending'})))
      setSkipped(f.skipped||[]); setIntensity(f.intensity||{}); setPhase('composed')
      localStorage.setItem('s3_steps',JSON.stringify(f.steps.map((s:string)=>({name:s,status:'pending'}))))
      localStorage.setItem('s3_skipped',JSON.stringify(f.skipped||[])); localStorage.setItem('s3_phase','composed')
    } catch { setChat(prev => [...prev, { role:'agent', text:'手动拼装失败' }]) }
  }

  // 收敛历史
  const [history, setHistory] = useState<any[]>([])
  const loadHistory = async () => {
    try {
      const r = await fetch(`${API}/api/runs/history`)
      const d = await r.json()
      setHistory(d.history || [])
    } catch {}
  }
  useEffect(() => { loadHistory() }, [phase])

  // GDS 预览 — 路径持久化, 组件自行加载 (刷新/切换页面后自动恢复)
  const [gdsPath, setGdsPath] = useState('')

  useEffect(() => { setGdsPath(localStorage.getItem('s3_gds_path') || '') }, [])

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
                  <div key={i} className={`flex items-center gap-1.5 text-[11px] py-0.5 px-1.5 rounded ${s.status==='running'?'bg-blue-900/20':s.status==='failed'?'bg-red-900/20':s.status==='done'?'bg-green-900/10':s.status==='skipped'?'bg-gray-800/50':''}`}>
                    <span>{s.status==='done'?'✅':s.status==='running'?'🔄':s.status==='failed'?'❌':s.status==='skipped'?'⏭️':'⏳'}</span>
                    <span className="text-gray-400">{(i+1)}. {s.name}</span>
                    {s.duration !== undefined && <span className="text-gray-600 ml-auto">{s.duration}s</span>}
                    {s.reason && s.status==='skipped' && <span className="text-yellow-500/80 ml-1 text-[10px] truncate max-w-[180px]" title={s.reason}>{s.reason}</span>}
                    {s.error && <span className="text-red-400 ml-1 text-[10px] truncate max-w-[120px]" title={s.error}>{s.error}</span>}
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

            {/* 活动 2: 收敛循环 — 方案 6.3.3 (诊断→回溯→重拼装→重跑) */}
            {convergence && convergence.rounds?.length > 0 && (
              <div className="bg-gray-900 border border-gray-700 rounded p-3">
                <h4 className="text-xs font-medium text-gray-300 mb-2">
                  🔁 收敛循环 ({convergence.rounds.length} 轮) · {convergence.status==='converged' ? <span className="text-green-400">✅ 收敛</span>
                  : convergence.status==='stop_loss' ? <span className="text-red-400">🛑 止损</span>
                  : <span className="text-yellow-400">⏱ 轮数上限</span>}
                </h4>
                <div className="space-y-1.5">
                  {convergence.rounds.map((rd:any) => {
                    const m = rd.metrics || {}
                    const d = rd.decision || {}
                    const diag = rd.diagnosis || {}
                    return (
                      <div key={rd.round} className={`rounded p-2 text-[10px] leading-relaxed ${d.type==='rerun'?'bg-blue-900/15 border border-blue-900/50':d.type==='converged'?'bg-green-900/15 border border-green-900/50':d.type==='stop'?'bg-red-900/15 border border-red-900/50':'bg-gray-800/40'}`}>
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-gray-300">R{rd.round}</span>
                          <span className="text-gray-500">{rd.round===1?'活动1 初始运行':'回溯重跑'} · {rd.frequency}MHz · 利用率{rd.utilization}</span>
                          <span className="ml-auto font-mono text-gray-400">
                            WNS={m.wns!==null&&m.wns!==undefined?m.wns+'ns':'—'} · DRC={m.drc!==null&&m.drc!==undefined?m.drc:'—'} · 面积={m.area??'—'}
                          </span>
                        </div>
                        {diag.problems?.length > 0 && (
                          <div className="mt-1 text-yellow-400/90">
                            🔍 诊断: {diag.problems.map((p:any)=>`${p.signal} (${p.severity})`).join('; ')}
                          </div>
                        )}
                        <div className={`mt-0.5 ${d.type==='rerun'?'text-blue-300':d.type==='converged'?'text-green-400':d.type==='stop'?'text-red-400':'text-yellow-400'}`}>
                          {d.type==='rerun' && <>🛠 决策: {d.reason}</>}
                          {d.type==='converged' && <>✅ {d.reason}</>}
                          {d.type==='stop' && <>🛑 止损: {d.reason}</>}
                          {d.type==='max_rounds' && <>⏱ {d.reason}</>}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
            {/* 活动 3: 归档交付 (方案 6.3.3) — 竞赛→PPA报告 / 流片→签核文档 / 科研→评估报告 */}
            {archive && archive.report_path && (
              <div className="bg-gray-900 border border-gray-700 rounded p-3">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-xs font-medium text-gray-300">
                    📦 归档交付 · {archive.title}
                  </h4>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${archive.status==='delivered'?'bg-green-900/30 text-green-400':'bg-yellow-900/30 text-yellow-400'}`}>
                    {archive.status==='delivered'?'✅ 可交付':'⚠️ 部分完成'}
                  </span>
                </div>
                <div className="flex gap-3 text-[10px] font-mono text-gray-400 mb-2">
                  {archive.metrics?.wns !== null && archive.metrics?.wns !== undefined && <span>WNS={archive.metrics.wns}ns</span>}
                  {archive.metrics?.area != null && <span>面积={archive.metrics.area}</span>}
                  {archive.metrics?.drc !== null && archive.metrics?.drc !== undefined && <span>DRC={archive.metrics.drc}</span>}
                </div>
                {archive.checklist?.length > 0 && (
                  <div className="space-y-0.5 mb-2">
                    {archive.checklist.map((c:any, i:number) => (
                      <div key={i} className="flex gap-1.5 text-[10px]">
                        <span>{c.state==='pass'?'✅':c.state==='fail'?'❌':'⏭️'}</span>
                        <span className={c.state==='pass'?'text-gray-400':c.state==='fail'?'text-red-400':'text-gray-600'}>{c.name}</span>
                        <span className="text-gray-600 ml-auto">{c.note}</span>
                      </div>
                    ))}
                  </div>
                )}
                <div className="text-[10px] text-gray-500 leading-relaxed mb-2">📝 {archive.conclusion}</div>
                <a href={withToken(`${API}/api/files/download?path=${encodeURIComponent(archive.report_path)}`)}
                   className="text-blue-400 hover:text-blue-300 text-[11px]" download>
                  ⬇ 下载交付报告 (Markdown)
                </a>
              </div>
            )}

            {/* 跑完但无收敛循环数据 → 说明原因, 避免用户以为功能缺失 */}
            {phase==='done' && (!convergence || !convergence.rounds?.length) && (
              <div className="bg-gray-900/50 rounded p-2 text-[10px] text-gray-500">
                ℹ️ 本次运行未触发收敛循环。收敛循环 (活动 2) 仅在 竞赛/科研/流片 场景、
                非 quick 深度、且包含 STA/物理步骤时启用 — 诊断出时序违例或 DRC 违例后会
                自动降频/降密度并回溯重跑 (最多 3 轮, standard)。
              </div>
            )}

            {/* 收敛历史 — P2-2: Recharts 指标趋势 (WNS/面积/DRC) */}
            {history.length > 1 && (
              <div className="bg-gray-900 border border-gray-700 rounded p-3">
                <h4 className="text-xs font-medium text-gray-300 mb-2">📈 收敛历史 ({history.length} 轮)</h4>
                <ConvergenceChart history={history} />
                <div className="flex gap-3 text-[10px] text-gray-500 mt-1">
                  {history.map((hr:any, i:number) => (
                    <span key={i} className={hr.steps_failed===0?'text-green-500':'text-red-500'}>
                      R{i+1}: {hr.steps_done}✓{hr.steps_failed>0?` ${hr.steps_failed}✗`:''}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* GDS 预览 — 持久化 + 缩放/平移/图层控制 */}
            {gdsPath && <GdsPreview path={gdsPath} />}
          </>
        )}
      </div>

      {/* ===== 右栏: 聊天 ===== */}
      <div className="w-[30%] flex flex-col bg-gray-900 border border-gray-700 rounded shrink-0 min-w-0">
        <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between"><h4 className="text-xs font-medium text-gray-300">🤖 AI 助手</h4><button onClick={clearChat} className="text-[10px] text-gray-500 hover:text-red-400 px-1.5 py-0.5 rounded" title="清空对话记录">🗑 清空</button></div>
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

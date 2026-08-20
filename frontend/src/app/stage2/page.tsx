'use client'
import { useState, useEffect, useRef } from 'react'
import { autosave } from '@/lib/autosave'
import { withToken } from '@/lib/authFetch'
import dynamic from 'next/dynamic'
import Markdown from '@/components/Markdown'
import WaveformSVG from '@/components/WaveformSVG'
import { awardStage2 } from '@/lib/badges'
import { addOp } from '@/lib/oplog'
const Editor = dynamic(() => import('@monaco-editor/react'), { ssr: false })
const API = 'http://localhost:8000'

type ChatMsg = {role:'user'|'agent',text:string}

export default function Stage2() {
  const [code, setCode] = useState('')
  const [tb, setTb] = useState('')
  const [results, setResults] = useState<any[]>([])
  const [loadingTB, setLoadingTB] = useState(false)
  const [loadingSim, setLoadingSim] = useState(false)
  const [loadingSVA, setLoadingSVA] = useState(false)
  const [loadingFormal, setLoadingFormal] = useState(false)
  // 自动激励与 TB 仿真结果独立保存, 并列展示互不覆盖
  const [autoResult, setAutoResult] = useState<any>(null)
  const [tbResult, setTbResult] = useState<any>(null)
  const [activeTab, setActiveTab] = useState<'sim'|'formal'>('sim')
  const [svaCode, setSvaCode] = useState('')
  const [formalResult, setFormalResult] = useState('')
  const [chat, setChat] = useState<ChatMsg[]>([])
  const [chatInput, setChatInput] = useState('')
  const [sampleCount, setSampleCount] = useState(20)
  // 仿真控制: 时钟周期 (后端 TB always #{p/2} → VCD/波形时间轴同源)
  const [clkPeriod, setClkPeriod] = useState(10)
  // 覆盖率 (方案 3.3): Verilator Line + Toggle, 激励与自动激励仿真同源
  const [coverage, setCoverage] = useState<any>(null)
  const [loadingCov, setLoadingCov] = useState(false)
  const editorRef = useRef<any>(null)
  const monacoRef = useRef<any>(null)

  // Monaco 源码覆盖率热力图: 绿=已覆盖 红=未覆盖
  const applyCoverageDecorations = (cov: any) => {
    if (!cov?.lines || !editorRef.current || !monacoRef.current) return
    const monaco = monacoRef.current
    const decs = Object.entries(cov.lines).map(([ln, hits]: [string, any]) => ({
      range: new monaco.Range(+ln, 1, +ln, 1),
      options: { isWholeLine: true, className: hits > 0 ? 'cov-hit' : 'cov-miss' },
    }))
    editorRef.current.deltaDecorations([], decs)
  }

  // 旧模板格式的 SVA (assert property/disable iff/##1/$stable) 本机 yosys 不支持,
  // 或完全没有断言内容 (纯注释) — 历史 localStorage 数据会导致 BMC 必失败 → 自动失效并提示重新生成
  const isOldSvaFormat = (s: string) => /assert\s*property|disable\s+iff|##\d|\$stable/.test(s)
    || !/\b(assert|assume|cover)\b/.test(s)

  useEffect(() => {
    setCode(localStorage.getItem('s1_code')||'')
    setTb(localStorage.getItem('s1_tb')||'')
    try { setResults(JSON.parse(localStorage.getItem('s2_results')||'[]')) } catch {}
    try { setChat(JSON.parse(localStorage.getItem('s2_chat')||'[]')) } catch {}
    try { setAutoResult(JSON.parse(localStorage.getItem('s2_auto_icarus')||'null')) } catch {}
    try { setTbResult(JSON.parse(localStorage.getItem('s2_tb_icarus')||'null')) } catch {}
    const sva = localStorage.getItem('s2_sva')
    // SVA 版本标记: 模板/语法经过多轮修复, 历史 SVA (任何格式) 一律失效重新生成
    if (sva && localStorage.getItem('s2_sva_v') !== '2') {
      localStorage.removeItem('s2_sva')
      localStorage.setItem('s2_sva_v', '2')
      setChat([{role:'agent', text:'⚠️ SVA 模板已升级（修复了复位时序/递减计数器/语法兼容等问题），历史 SVA 已自动失效。请点击「🤖 AI 生成 SVA」重新生成后再跑 BMC。'}])
    } else if (sva && isOldSvaFormat(sva)) {
      // 旧格式已无法通过 BMC, 清除并提示
      localStorage.removeItem('s2_sva')
      setChat([{role:'agent', text:'⚠️ 检测到历史 SVA 是旧模板格式（本机 yosys 不支持 assert property/## 语法），已自动清除。请点击「🤖 AI 生成 SVA」重新生成后再跑 BMC。'}])
    } else if (sva) setSvaCode(sva)
    const fr = localStorage.getItem('s2_fr'); if (fr) setFormalResult(fr)
    try { const cov = JSON.parse(localStorage.getItem('s2_coverage')||'null'); if (cov) setCoverage(cov) } catch {}
  }, [])

  const S = (k:string,v:string)=>{localStorage.setItem(k,v)}
  const saveChat = (c:ChatMsg[])=>{setChat(c);S('s2_chat',JSON.stringify(c))}
  const clearChat = async () => {
    // 清空本地 + 服务端会话, 旧对话不干扰新环境
    setChat([]); localStorage.removeItem('s2_chat')
    try { await fetch(`${API}/api/chat/clear`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:'stage2'})}) } catch {}
  }

  const sendChat = async (msg?:string) => {
    const m = msg || chatInput.trim(); if (!m) return
    const u:ChatMsg = {role:'user',text:m}; const updated = [...chat,u]
    saveChat(updated); setChatInput('')
    try {
      const ctx = [`RTL: ${code.slice(0,800)}`, `SVA: ${svaCode.slice(0,500)}`, formalResult ? `SBY结果: ${formalResult}` : ''].filter(Boolean).join('\n---\n')
      const r = await fetch(`${API}/api/chat`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:m,session_id:'stage2',context:ctx})})
      const d = await r.json()
      saveChat([...updated,{role:'agent',text:d.reply}])
    } catch { saveChat([...updated,{role:'agent',text:'连接失败'}]) }
  }

  const genTB = async () => { setLoadingTB(true)
    try { const r=await fetch(`${API}/api/rtl/generate-tb`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:'testbench',verilog:code})});const d=await r.json();setTb(d.testbench||'生成失败');if(d.testbench){S('s1_tb',d.testbench);autosave('tb.v',d.testbench)} } catch {}
    setLoadingTB(false) }

  const runSimWith = async (mode: 'auto'|'tb') => { setLoadingSim(true)
    // P1-6: WebSocket 实时步骤推送; auto 与 tb 两种模式独立并行
    const myRunId = 'run_' + Math.random().toString(36).slice(2, 10)
    let ws: WebSocket | null = null
    try {
      ws = new WebSocket(`ws://localhost:8000/ws/${myRunId}`)
      ws.onmessage = (e) => {
        try {
          const ev = JSON.parse(e.data)
          if (ev.type === 'step_start') {
            setResults(prev => prev.some(s => s.step === ev.step) ? prev.map(s => s.step===ev.step?{...s,status:'running'}:s) : [...prev,{step:ev.step,status:'running',duration:0}])
          } else if (ev.type === 'step_done') {
            setResults(prev => prev.map(s => s.step===ev.step?{...s,status:ev.status==='skipped'?'skipped':(ev.status==='failed'||ev.success===false)?'failed':'done',success:ev.success,duration:ev.duration}:s))
          }
        } catch {}
      }
    } catch {}
    try {
      const r1=await fetch(`${API}/api/flow/compose`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene:'experience',design:'sim_test',rtl_code:code})});const f=await r1.json()
          // 参考模型选择: 代码与模型绑定的代码快照一致才复用 (否则现场重新生成)
          // - 阶段2 最近验证: s2_py_model + s2_py_code
          // - 阶段1 生成时: s1_py_model + s1_py_code
          const pyModel = code === localStorage.getItem('s2_py_code') ? (localStorage.getItem('s2_py_model')||'')
                        : code === localStorage.getItem('s1_py_code') ? (localStorage.getItem('s1_py_model')||'')
                        : ''
          const body:any={flow_id:f.flow_id,rtl_code:code,params:{run_id:myRunId}}
          if(mode==='tb'){ body.tb_code=tb }
          else {
            body.params={sample_count:sampleCount,clk_period_ns:clkPeriod,run_id:myRunId,py_model:pyModel}
          }
          const r2=await fetch(`${API}/api/flow/run`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const run=await r2.json()
          const icarusStep=(run.results||[]).find((r:any)=>r.step==='icarus_sim')||null
          if(mode==='tb'){ setTbResult(icarusStep); S('s2_tb_icarus',JSON.stringify(icarusStep))
            addOp(2, `TB 仿真 ${icarusStep?.success ? '✅ 通过' : '❌ 失败'}`, !!icarusStep?.success) }
          else {
            setAutoResult(icarusStep); S('s2_auto_icarus',JSON.stringify(icarusStep))
            awardStage2(icarusStep)  // 通关徽章: 铜=仿真 / 银=波形100% / 金=形式验证
            const mr = (icarusStep?.reason || '').match(/匹配率 (\d+)%/)
            addOp(2, `自动激励仿真 ${icarusStep?.success ? `✅ 匹配率 ${mr?.[1] || '—'}%` : '❌ 失败'}`, !!icarusStep?.success)
            // 把本次使用的参考模型与代码快照绑定存下, 下次代码没变直接复用
            if(icarusStep?.py_model){ S('s2_py_model',icarusStep.py_model); S('s2_py_code',code) }
          }
          setResults(run.results||[]);S('s2_results',JSON.stringify(run.results||[])) } catch(e:any){setResults([{step:'error',status:'failed',error:String(e.message)}])}
    ws?.close()
    setLoadingSim(false) }

  const runCoverage = async () => {
    // 覆盖率需要自动激励仿真的同源激励向量
    const detail = autoResult?.detail
    if (!detail?.stimuli || !code) return
    setLoadingCov(true)
    try {
      const r = await fetch(`${API}/api/coverage/run`, { method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ rtl_code: code, inputs: detail.inputs||[], stimuli: detail.stimuli, has_clk: detail.has_clk!==false, vcd_path: detail.vcd_path || '' }) })
      const d = await r.json()
      setCoverage(d); S('s2_coverage', JSON.stringify(d))
      applyCoverageDecorations(d)
      if (d.success) {
        addOp(2, `覆盖率 Line ${d.line_pct}% · Branch ${d.branch_pct ?? '—'}% · FSM ${d.fsm?.pct ?? '—'}%`)
      } else { setChat(prev=>[...prev,{role:'agent',text:`📊 覆盖率收集失败: ${d.error||'未知错误'}`}]) }
    } catch { setChat(prev=>[...prev,{role:'agent',text:'📊 覆盖率收集失败: 连接失败'}]) }
    setLoadingCov(false) }

  const [svaRound, setSvaRound] = useState(0)
  const [svaAnalysis, setSvaAnalysis] = useState('')

  const genSVA = async () => { if(!code)return; setLoadingSVA(true)
    try { const r=await fetch(`${API}/api/rtl/generate-sva`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:code,context:{round:svaRound}})});const d=await r.json();const v=d.sva||'生成失败';const fresh='`ifdef FORMAL\n'+v+'\n`endif'; setSvaCode(fresh);S('s2_sva',fresh);S('s2_sva_v','2');autosave('properties.sva',fresh);if(d.analysis)setSvaAnalysis(d.analysis);if(d.method==='template')setSvaRound(d.round||1);const methods: Record<string,string> = {'template':'模板匹配','llm':'LLM 生成','none':'无可用方法'};setChat(prev=>[...prev,{role:'agent',text:`SVA (${methods[d.method]||d.method}): 检测到 ${d.analysis||'标准结构'}${d.templates_used?.length?', 使用模板: '+d.templates_used.join(', '):''}。${d.next_round_available?'可再次点击生成更多。':''}`}]) } catch {}
    setLoadingSVA(false) }

  const runFormal = async () => { setLoadingFormal(true)
    // 旧格式 SVA 本机 yosys 必失败 → 前置拦截, 提示重新生成
    if (isOldSvaFormat(svaCode)) {
      setFormalResult('⚠️ 当前 SVA 是旧模板格式（本机 yosys 不支持 assert property/disable iff/##1/$stable 语法）。请先点击「🤖 AI 生成 SVA」重新生成，再运行 BMC。')
      S('s2_fr', formalResult)
      setLoadingFormal(false)
      return
    }
    // P1-6: WebSocket 实时步骤推送 + formal_only (只跑 lint+sby, 不跑物理流程)
    // 注意: 形式验证结果不覆盖功能仿真结果 (s2_results), 单独记录 s2_fr_pass
    const myRunId = 'run_' + Math.random().toString(36).slice(2, 10)
    let ws: WebSocket | null = null
    try {
      ws = new WebSocket(`ws://localhost:8000/ws/${myRunId}`)
      ws.onmessage = () => {}  // 形式验证步骤状态由 formalResult 展示
    } catch {}
    try { const r1=await fetch(`${API}/api/flow/compose`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene:'research',design:'formal_check',rtl_code:code})});const f=await r1.json()
          const r2=await fetch(`${API}/api/flow/run`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({flow_id:f.flow_id,rtl_code:code+'\n'+svaCode,params:{formal_mode:'bmc',formal_depth:10,formal_only:true,run_id:myRunId}})});const run=await r2.json()
          const sby=run.results?.find((r:any)=>r.step==='sby_check')
          const fr=sby?.summary||sby?.stdout?.slice(0,500)||sby?.reason||JSON.stringify(run.results,null,2)
          setFormalResult(fr);S('s2_fr',fr)
          // 形式验证通关标记 (阶段3/右栏判定用): 1=通过 0=未通过
          S('s2_fr_pass', sby?.success ? '1' : '0')
          awardStage2(autoResult)  // 形式验证通过 → 阶段2 金牌
          addOp(2, `形式验证 BMC ${sby?.success ? '✅ PASS' : '❌ FAIL'}`, !!sby?.success)
        } catch(e:any){const fr='失败: '+e.message;setFormalResult(fr);S('s2_fr',fr);S('s2_fr_pass','0')}
    ws?.close()
    setLoadingFormal(false) }

  const icarus=results.find((r:any)=>r.step==='icarus_sim')
  const verilator=results.find((r:any)=>r.step==='verilator_lint')
  const verible=results.find((r:any)=>r.step==='verible_lint')

  // 问题清单 (QA-008 简化版): 自动从结果生成, 用户可标记修复/忽略
  const [issues, setIssues] = useState<{id:number, step:string, desc:string, status:'open'|'waived'|'fixed'}[]>([])
  useEffect(()=>{
    if (results.length === 0) { setIssues([]); return }
    const list:any[] = []
    if (verible && verible.violations > 0) list.push({id:1, step:'verible_lint', desc:`${verible.violations} 个 lint 违例`, status:'open'})
    if (verilator && verilator.errors > 0) list.push({id:2, step:'verilator_lint', desc:`${verilator.errors} 个编译错误`, status:'open'})
    if (icarus && icarus.success === false) list.push({id:3, step:'icarus_sim', desc:'仿真失败', status:'open'})
    setIssues(list)
  }, [results])
  const setIssueStatus = (id:number, status:'open'|'waived'|'fixed') => {
    setIssues(prev => prev.map(i => i.id===id ? {...i, status} : i))
  }

  return (<div className="max-w-4xl mx-auto space-y-4">
    <h2 className="text-lg font-bold text-blue-400">🔍 阶段 2: 仿真与验证</h2>
    {results.length>0&&(<div className="grid grid-cols-3 gap-2 text-xs"><RC l="Verible Lint" ok={verible?.violations===0} d={`违例:${verible?.violations??'?'}`}/><RC l="Verilator" ok={verilator?.errors===0} d={`Error:${verilator?.errors??'?'}`}/><RC l="Icarus 仿真" ok={icarus?.success} d={icarus?.assertions_ok?'断言通过':icarus?.reason||''}/></div>)}

    {/* ===== 问题清单 (QA-008) ===== */}
    {issues.length > 0 && (
      <div className="bg-gray-900 border border-gray-700 rounded p-3">
        <h4 className="text-sm font-medium text-gray-300 mb-2">🐛 问题清单 ({issues.filter(i=>i.status==='open').length} 待处理)</h4>
        {issues.map(i => (
          <div key={i.id} className={`flex items-center gap-2 py-1.5 text-xs border-b border-gray-800/50 last:border-0 ${i.status==='waived'?'opacity-50':''}`}>
            <span>{i.status==='fixed'?'✅':i.status==='waived'?'🚫':'🔧'}</span>
            <span className="flex-1 text-gray-300">{i.desc} <span className="text-gray-600">({i.step})</span></span>
            {i.status==='open' && (
              <div className="flex gap-1">
                <button onClick={()=>setIssueStatus(i.id,'fixed')} className="bg-green-700 px-2 py-0.5 rounded text-[10px]">修复</button>
                <button onClick={()=>setIssueStatus(i.id,'waived')} className="bg-gray-700 px-2 py-0.5 rounded text-[10px]">忽略</button>
              </div>
            )}
            {i.status!=='open' && <button onClick={()=>setIssueStatus(i.id,'open')} className="text-gray-500 text-[10px] hover:text-white">撤销</button>}
          </div>
        ))}
      </div>
    )}
    <div className="flex gap-1 bg-gray-900 rounded p-1 w-fit"><button onClick={()=>setActiveTab('sim')} className={`px-3 py-1 rounded text-xs ${activeTab==='sim'?'bg-blue-600':''}`}>📊 功能仿真</button><button onClick={()=>setActiveTab('formal')} className={`px-3 py-1 rounded text-xs ${activeTab==='formal'?'bg-blue-600':''}`}>🔷 形式化验证</button></div>
    {activeTab==='sim'?(
      <div className="space-y-3">
        <div className="border border-gray-700 rounded overflow-hidden" style={{height:'min(25vh,220px)',resize:'vertical'} as any}><Editor language="verilog" value={code} onChange={v=>{if(v){setCode(v);S('s1_code',v)}}} theme="vs-dark" options={{fontSize:11,minimap:{enabled:false},scrollBeyondLastLine:false}} height="100%" onMount={(ed:any,monaco:any)=>{editorRef.current=ed;monacoRef.current=monaco;applyCoverageDecorations(coverage)}}/></div>
        <div><div className="flex gap-1 mb-1"><button onClick={genTB} disabled={loadingTB||!code} className="bg-blue-600 px-2 py-0.5 rounded text-xs disabled:bg-gray-700">{loadingTB?'...':'🤖 AI 生成 TB'}</button><button onClick={()=>{setTb('');localStorage.removeItem('s1_tb')}} className="bg-red-700 px-2 py-0.5 rounded text-xs" title="清空TB以使用自动激励">✕ 清空</button></div>
        <div className="border border-gray-700 rounded overflow-hidden" style={{height:'min(35vh,320px)',resize:'vertical'} as any}><Editor language="verilog" value={tb} onChange={v=>{if(v){setTb(v);S('s1_tb',v);autosave('tb.v',v)}}} theme="vs-dark" options={{fontSize:11,minimap:{enabled:false},scrollBeyondLastLine:false}} height="100%"/></div></div>
        <div className="flex items-center gap-3 flex-wrap">
          {/* 两种仿真模式并列: 自动激励 (参考模型对照) 与 TB 仿真 (定向测试), 结果独立展示 */}
          <button onClick={()=>runSimWith('auto')} disabled={loadingSim||!code} title="平台随机激励 + Python 参考模型对照"
            className="bg-green-600 px-4 py-2 rounded text-sm disabled:bg-gray-700">{loadingSim?'...':'▶ 自动激励仿真'}</button>
          {/* 仿真控制: 采样点数 (时间范围) + 时钟周期 (时间步进) */}
          <div className="flex items-center gap-2 text-[10px] text-gray-500 bg-gray-800/40 rounded px-2 py-1" title="采样点 × 时钟周期 = 仿真总时长; 波形横轴与 VCD 都按此周期">
            <span>采样点:</span>
            <input type="range" min="5" max="100" value={sampleCount} onChange={e => setSampleCount(+e.target.value)} className="w-16 accent-blue-500" />
            <span className="text-gray-400 w-5">{sampleCount}</span>
            <span className="text-gray-700">|</span>
            <span>时钟周期:</span>
            <select value={clkPeriod} onChange={e => setClkPeriod(+e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-1 py-0.5 text-[10px]">
              {[10, 20, 40, 100].map(t => <option key={t} value={t}>{t}ns</option>)}
            </select>
            <span>总时长 <span className="text-gray-300">{sampleCount * clkPeriod}ns</span></span>
          </div>
          <button onClick={()=>runSimWith('tb')} disabled={loadingSim||!code||!tb.trim()} title={tb.trim()?'运行你/AI 编写的 testbench 定向测试':'需先 🤖 AI 生成 TB 或手写 TB'}
            className="bg-blue-600 px-4 py-2 rounded text-sm disabled:bg-gray-700 disabled:cursor-not-allowed">{loadingSim?'...':'▶ TB 仿真'}</button>
          <button onClick={runCoverage} disabled={loadingCov||!autoResult?.detail?.stimuli} title={autoResult?.detail?.stimuli?'Verilator 覆盖率 (Line/Toggle), 激励与自动激励仿真同源':'需先运行自动激励仿真 (覆盖率激励与它同源)'}
            className="bg-purple-600 px-4 py-2 rounded text-sm disabled:bg-gray-700 disabled:cursor-not-allowed">{loadingCov?'...':'📊 覆盖率'}</button>
          <details className="text-[10px] text-gray-500">
            <summary className="cursor-pointer hover:text-gray-300">❓ 自动激励仿真和波形是什么关系?</summary>
            <div className="mt-1 space-y-1 bg-gray-900/60 rounded p-2 leading-relaxed">
              <p>1️⃣ <b className="text-gray-300">自动激励仿真</b>：平台随机生成 N 组输入（激励）→ 同时喂给两套"计算器"：你的 Verilog 仿真 和 AI 生成的 Python 参考模型 → 逐组对比输出值 → 全部一致 = 匹配率 100%，证明你的 RTL 行为正确。</p>
              <p>2️⃣ <b className="text-gray-300">波形</b>：N 个采样点的时序图 —— 横轴是时间（每采样点一个时钟周期，周期在上方仿真控制中调整），纵轴是信号值。你能直观看到 q 在什么输入下跳变、复位后是否清零。</p>
              <p>3️⃣ <b className="text-gray-300">TB 仿真与自动激励的区别</b>：TB 仿真跑的是你（或 AI 生成）的 testbench —— 激励由 TB 代码定向指定（如专门测复位、测使能边沿），验证依据是 TB 里的断言打印；自动激励则是随机向量 + 参考模型对照。两者可并列运行、结果互不覆盖。</p>
              <p>4️⃣ <b className="text-gray-300">VCD 文件</b>：记录每个时钟沿的完整波形，下载后用 GTKWave / VS Code WaveTrace 插件打开可放大细看。</p>
            </div>
          </details>
        </div>
        {/* 覆盖率面板 (方案 3.3): Line/Toggle 真实数据, Branch/FSM 如实标注工具限制 */}
        {coverage && (
          <div className="bg-gray-900 border border-gray-700 rounded p-3 text-xs space-y-2">
            <div className="flex items-center justify-between">
              <h4 className="font-medium text-gray-300">📊 覆盖率 (Verilator · 与自动激励同源)</h4>
              <span className={`text-[10px] ${coverage.success ? 'text-gray-500' : 'text-red-400'}`}>
                {coverage.success ? '' : `收集失败: ${coverage.error||''}`}
              </span>
            </div>
            {coverage.success && (
              <>
                <div className="grid grid-cols-4 gap-2">
                  {[
                    { l:'Line 语句行', pct: coverage.line_pct, sub: `${coverage.line_covered}/${coverage.line_total} 行`,
                      hint: '源码中绿色=已执行, 红色=未覆盖' },
                    { l:'Toggle 翻转', pct: coverage.toggle_pct, sub: `${coverage.toggle_covered}/${coverage.toggle_total} 信号`,
                      hint: '信号是否发生 0↔1 翻转' },
                    { l:'Branch 分支臂', pct: coverage.branch_pct, sub: coverage.branch_total ? `${coverage.branch_covered}/${coverage.branch_total} 臂` : '无分支结构',
                      hint: 'if/elsif/else 各分支臂是否被执行过 (Verilator 臂级行点, 与 Line 同源同激励)' },
                    { l:'FSM 状态寄存器', pct: coverage.fsm?.pct ?? null, sub: coverage.fsm?.regs?.length ? `${coverage.fsm.regs.length} 个寄存器` : (coverage.fsm?.error ? '无 VCD 数据' : '未收集'),
                      hint: '状态寄存器达到的不同取值数 / 取值域 (FSM 状态由寄存器编码, 来自同一次仿真的 VCD)' },
                  ].map((c, i) => (
                    <div key={i} className="bg-gray-800/40 rounded p-2" title={c.hint}>
                      <div className="text-gray-500 text-[10px]">{c.l}</div>
                      {c.pct !== null && c.pct !== undefined ? (
                        <>
                          <div className="text-sm font-bold text-purple-300 mt-0.5">{c.pct}%</div>
                          <div className="h-1 bg-gray-700 rounded mt-1 overflow-hidden">
                            <div className="h-full bg-purple-500 rounded" style={{width: `${Math.min(c.pct, 100)}%`}}/>
                          </div>
                          <div className="text-[9px] text-gray-600 mt-0.5">{c.sub}</div>
                        </>
                      ) : <div className="text-[10px] text-gray-600 mt-1">{c.sub}</div>}
                    </div>
                  ))}
                </div>
                {coverage.fsm?.regs?.length > 0 && (
                  <details className="text-[10px] text-gray-500">
                    <summary className="cursor-pointer hover:text-gray-300">📋 状态寄存器明细</summary>
                    <div className="mt-1 space-y-0.5 font-mono">
                      {coverage.fsm.regs.map((rg:any, i:number) => (
                        <div key={i}>{rg.name} ({rg.width}bit): 到达 {rg.distinct}/{rg.total ?? '—'} 个取值 {rg.pct!==null&&rg.pct!==undefined?`= ${rg.pct}%`:''}</div>
                      ))}
                    </div>
                  </details>
                )}
                {coverage.line_pct !== null && (
                  <div className="text-[10px] text-gray-500">
                    Line: {coverage.line_covered}/{coverage.line_total} 行 · Toggle: {coverage.toggle_covered}/{coverage.toggle_total} 信号
                    · 绿色=已覆盖 (行内数字为命中次数), 红色=未覆盖 — 标注直接显示在上方源码编辑器
                  </div>
                )}
              </>
            )}
          </div>
        )}
        {(() => { const _block = (icarus: any, title: string) => <div className="bg-gray-950 rounded p-3 text-xs space-y-3"><div className="flex items-center gap-2"><span className="text-gray-400 font-medium shrink-0">{title}</span><span className={icarus.success?'text-green-400':'text-red-400'}>{icarus.success?'✅ 仿真通过':'❌ 仿真失败'}</span><span className="text-gray-500">{icarus.reason||''}</span></div>
          {(()=>{
            // ChipMATE detail: 结构化信号数据 (signals: {output_name: [values]})
            const detail = (icarus as any).detail
            if (detail && detail.signals) {
              const outNames = detail.outputs || Object.keys(detail.signals)
              const mainOut = outNames[0]
              const vals = detail.signals[mainOut] || []
              if (vals.length === 0) return <div className="text-gray-500">无信号数据</div>
              // 从 stimuli 提取输入信号
              const inputs = detail.inputs || []
              const inputSigs: Record<string, number[]> = {}
              if (detail.stimuli) {
                for (const inName of inputs) {
                  if (inName === 'clk') continue
                  // 低有效复位 (rst_n/rst_b/rst_l) 缺省值应为 1 (已解复位)
                  const isActiveLow = inName.endsWith('_n') || inName.endsWith('_b') || inName.endsWith('_l')
                  inputSigs[inName] = detail.stimuli.map((s:any) => s[inName] ?? (isActiveLow ? 1 : 0))
                }
              }
              const hasInputs = Object.keys(inputSigs).length > 0
              const vcdPath = (detail as any).vcd_path || ''
              const timeStep = (detail as any).time_step_ns || 10  // P3: 后端透传实际采样周期
              return <div className="space-y-2">
                <div className="flex items-center gap-3 text-gray-400 text-[10px]">
                  <span>{vals.length}采样点</span>
                  <span>{mainOut}: {Math.min(...vals)}~{Math.max(...vals)}</span>
                  <span>{vals.length*timeStep}ns (每采样{timeStep}ns)</span>
                  {vcdPath && (
                    <a href={withToken(`${API}/api/files/download?path=${encodeURIComponent(vcdPath)}`)}
                       className="text-blue-400 hover:text-blue-300 ml-auto" download>
                      ⬇ 下载 VCD 波形文件
                    </a>
                  )}
                </div>
                {/* 数字输入信号值行 — 最多显示前 20 组, 完整数据看波形/激励明细表 */}
                {hasInputs && <div className="space-y-0.5 text-[10px]">
                  <div className="text-gray-500 font-mono">{mainOut}: {vals.slice(0,20).map((v:number,i:number)=><span key={i} className="text-yellow-400 ml-0.5">{v}</span>)}{vals.length>20 && <span className="text-gray-600 ml-1">…(+{vals.length-20} 组, 见下方波形与激励明细)</span>}</div>
                  {Object.entries(inputSigs).map(([name, arr]) => (
                    <div key={name} className="text-gray-500 font-mono">{name}: {(arr as number[]).slice(0,20).map((v:number,i:number)=><span key={i} className="text-blue-400 ml-0.5">{v}</span>)}</div>
                  ))}
                </div>}
                <WaveformSVG values={vals} rst_n={inputSigs.rst_n} en={inputSigs.en} clk={inputSigs.clk} timeStep={timeStep} />
                {/* 激励明细: 每组输入是什么、对应输出多少, 数字直接可见 */}
                {detail.stimuli && detail.stimuli.length > 0 && (
                  <details className="text-[10px] text-gray-500">
                    <summary className="cursor-pointer hover:text-gray-300">📋 查看激励明细 ({detail.stimuli.length} 组测试向量)</summary>
                    <div className="overflow-x-auto mt-1 max-h-40 overflow-y-auto">
                      <table className="w-full border-collapse">
                        <thead>
                          <tr className="text-gray-400">
                            <th className="border border-gray-700 px-2 py-1 text-left">#</th>
                            {detail.inputs.filter((n:string)=>n!=='clk').map((n:string)=>(
                              <th key={n} className="border border-gray-700 px-2 py-1 text-left">输入 {n}</th>
                            ))}
                            <th className="border border-gray-700 px-2 py-1 text-left">输出 {mainOut}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detail.stimuli.slice(0, 30).map((s:any, i:number) => (
                            <tr key={i} className="text-gray-400 font-mono">
                              <td className="border border-gray-700 px-2 py-0.5">{i+1}</td>
                              {detail.inputs.filter((n:string)=>n!=='clk').map((n:string)=>(
                                <td key={n} className="border border-gray-700 px-2 py-0.5 text-yellow-400">{s[n] ?? (n.endsWith('_n')||n.endsWith('_b')||n.endsWith('_l') ? 1 : 0)}</td>
                              ))}
                              <td className="border border-gray-700 px-2 py-0.5 text-green-400">{detail.sv_results?.[i]?.[mainOut] ?? '-'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {detail.stimuli.length > 30 && <p className="mt-1">仅显示前 30 组, 完整数据见 VCD 文件</p>}
                    </div>
                  </details>
                )}
              </div>
            }
            // 旧格式 fallback: 解析 stdout 的 SIG/V= 格式 (用户 TB 路径, 无时间信息)
            if (icarus.stdout) {
              const sigs = { rst_n: [] as number[], en: [] as number[], q: [] as number[] }
              const sigRe = /SIG rst_n=(\d+) en=(\d+) q=\s*(\d+)/g
              let m; while ((m = sigRe.exec(icarus.stdout)) !== null) { sigs.rst_n.push(+m[1]); sigs.en.push(+m[2]); sigs.q.push(+m[3]) }
              if (sigs.q.length === 0) { const vm = icarus.stdout.match(/V=\s*(\d+)/g); if (vm) sigs.q = vm.map((v:string)=>parseInt(v.replace(/V=\s*/,''))) }
              const vals = sigs.q
              if (vals.length > 0) return <div className="space-y-2">
                <div className="text-[10px] text-gray-600">⚠️ 用户 TB 路径无时间信息, 时间轴为采样序号</div>
                <WaveformSVG values={vals} rst_n={sigs.rst_n} en={sigs.en} />
              </div>
            }
            return <div className="text-gray-500">无波形数据</div>
          })()}
        </div>; return (<div className="space-y-2">{autoResult ? _block(autoResult, '⚡ 自动激励仿真') : null}{tbResult ? _block(tbResult, '🧪 TB 仿真') : null}</div>); })()}
      </div>
    ):(
      <div className="space-y-3">
        <div className="flex gap-1"><button onClick={genSVA} disabled={loadingSVA||!code} className="bg-blue-600 px-2 py-0.5 rounded text-xs disabled:bg-gray-700">{loadingSVA?'...':'🤖 AI 生成 SVA'}</button></div>
        <p className="text-xs text-gray-500">SymbiYosys 形式验证。AI 自动生成 SVA property，工具证明是否成立。</p>
        <textarea value={svaCode} onChange={e=>{setSvaCode(e.target.value);S('s2_sva',e.target.value)}} className="w-full h-20 bg-gray-900 border border-gray-700 rounded p-3 font-mono text-xs" placeholder={"assert property (@posedge clk) q <= 4'd15;"}/>
        <button onClick={runFormal} disabled={loadingFormal||!code||!svaCode} className="bg-purple-600 px-4 py-2 rounded text-sm disabled:bg-gray-700">{loadingFormal?'...':'▶ SymbiYosys BMC'}</button>
        {formalResult&&(<div className="space-y-2"><pre className="bg-gray-950 rounded p-3 text-xs text-gray-400 font-mono max-h-32 overflow-auto whitespace-pre-wrap">{formalResult}</pre><div className="flex gap-1 flex-wrap"><button onClick={()=>sendChat(`形式验证结果: ${formalResult}。帮我分析一下为什么失败/成功，如果需要修改SVA或RTL，给出具体建议。`)} className="bg-blue-600 px-3 py-1 rounded text-xs">🤖 AI 分析</button>{formalResult.includes('失败')&&<button onClick={()=>{setSvaRound(0);setSvaCode('');genSVA()}} className="bg-yellow-600 px-3 py-1 rounded text-xs">🔄 重新生成SVA</button>}</div></div>)}
        <div className="bg-gray-900 border border-gray-700 rounded" style={{resize:"vertical",overflow:"hidden",display:"flex",flexDirection:"column",height:"250px",minHeight:"150px"}}><div className="px-2 py-1 border-b border-gray-800 text-xs text-gray-400 flex items-center justify-between">💬 AI 助手{chat.length>0&&<button onClick={clearChat} className="text-[10px] text-gray-500 hover:text-red-400 px-1 rounded" title="清空对话记录">🗑 清空</button>}</div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1.5">{chat.length===0&&<p className="text-[11px] text-gray-600">验证出结果后点"AI 帮我分析"，或直接问问题</p>}
            {chat.map((m,i)=>(<div key={i} className={`text-[11px] ${m.role==='agent'?'text-gray-300':'text-blue-400'}`}><span className="text-gray-600">{m.role==='agent'?'🤖':'👤'}</span> {m.role==='agent' ? <Markdown text={m.text} /> : <span className='whitespace-pre-wrap'>{m.text}</span>}</div>))}
          </div>
          <div className="border-t border-gray-800 p-1 flex gap-1"><input value={chatInput} onChange={e=>setChatInput(e.target.value)} onKeyDown={e=>e.key==='Enter'&&sendChat()} className="flex-1 bg-gray-800 rounded px-2 py-1 text-[11px] focus:outline-none" placeholder="问 AI..."/><button onClick={()=>sendChat()} className="bg-blue-600 px-2 py-0.5 rounded text-[11px]">发送</button></div>
        </div>
      </div>
    )}
  </div>)
}

function RC({l,ok,d}:{l:string,ok?:boolean,d?:string}){return<div className={`rounded p-2 ${ok===true?'bg-green-900/30 border border-green-700':ok===false?'bg-red-900/30 border border-red-700':'bg-gray-900/50 border border-gray-700'}`}><div className="font-medium text-gray-300">{l}</div><div className="text-gray-500 mt-0.5">{d}</div></div>}

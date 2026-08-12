'use client'
import { useState, useEffect } from 'react'
import { autosave } from '@/lib/autosave'
import dynamic from 'next/dynamic'
import Markdown from '@/components/Markdown'
import WaveformSVG from '@/components/WaveformSVG'
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
  const [activeTab, setActiveTab] = useState<'sim'|'formal'>('sim')
  const [svaCode, setSvaCode] = useState('')
  const [formalResult, setFormalResult] = useState('')
  const [chat, setChat] = useState<ChatMsg[]>([])
  const [chatInput, setChatInput] = useState('')
  const [sampleCount, setSampleCount] = useState(20)

  useEffect(() => {
    setCode(localStorage.getItem('s1_code')||'')
    setTb(localStorage.getItem('s1_tb')||'')
    try { setResults(JSON.parse(localStorage.getItem('s2_results')||'[]')) } catch {}
    try { setChat(JSON.parse(localStorage.getItem('s2_chat')||'[]')) } catch {}
    const sva = localStorage.getItem('s2_sva'); if (sva) setSvaCode(sva)
    const fr = localStorage.getItem('s2_fr'); if (fr) setFormalResult(fr)
  }, [])

  const S = (k:string,v:string)=>{localStorage.setItem(k,v)}
  const saveChat = (c:ChatMsg[])=>{setChat(c);S('s2_chat',JSON.stringify(c))}

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

  const runSim = async () => { setLoadingSim(true)
    try {
      const r1=await fetch(`${API}/api/flow/compose`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene:'experience',design:'sim_test'})});const f=await r1.json()
          const body:any={flow_id:f.flow_id,rtl_code:code}
          if(tb && tb.trim())body.tb_code=tb; else body.params={sample_count:sampleCount}
          const r2=await fetch(`${API}/api/flow/run`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const run=await r2.json()
          setResults(run.results||[]);S('s2_results',JSON.stringify(run.results||[])) } catch(e:any){setResults([{step:'error',status:'failed',error:String(e.message)}])}
    setLoadingSim(false) }

  const [svaRound, setSvaRound] = useState(0)
  const [svaAnalysis, setSvaAnalysis] = useState('')

  const genSVA = async () => { if(!code)return; setLoadingSVA(true)
    try { const r=await fetch(`${API}/api/rtl/generate-sva`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:code,context:{round:svaRound}})});const d=await r.json();const v=d.sva||'生成失败';const wrapper='`ifdef FORMAL\n'+v+'\n`endif';const fresh = svaCode.includes('`ifdef FORMAL') ? wrapper : wrapper; setSvaCode(fresh);S('s2_sva',fresh);autosave('properties.sva',fresh);if(d.analysis)setSvaAnalysis(d.analysis);if(d.method==='template')setSvaRound(d.round||1);const methods={'template':'模板匹配','llm':'LLM 生成','none':'无可用方法'};setChat(prev=>[...prev,{role:'agent',text:`SVA (${methods[d.method]||d.method}): 检测到 ${d.analysis||'标准结构'}${d.templates_used?.length?', 使用模板: '+d.templates_used.join(', '):''}。${d.next_round_available?'可再次点击生成更多。':''}`}]) } catch {}
    setLoadingSVA(false) }

  const runFormal = async () => { setLoadingFormal(true)
    try { const r1=await fetch(`${API}/api/flow/compose`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene:'research',design:'formal_check'})});const f=await r1.json()
          const r2=await fetch(`${API}/api/flow/run`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({flow_id:f.flow_id,rtl_code:code+'\n'+svaCode,params:{formal_mode:'bmc',formal_depth:10}})});const run=await r2.json()
          const sby=run.results?.find((r:any)=>r.step==='sby_check');const fr=sby?.summary||sby?.stdout?.slice(0,500)||sby?.reason||JSON.stringify(run.results,null,2)
          setFormalResult(fr);S('s2_fr',fr) } catch(e:any){const fr='失败: '+e.message;setFormalResult(fr);S('s2_fr',fr)}
    setLoadingFormal(false) }

  const icarus=results.find((r:any)=>r.step==='icarus_sim')
  const verilator=results.find((r:any)=>r.step==='verilator_lint')
  const verible=results.find((r:any)=>r.step==='verible_lint')

  return (<div className="max-w-4xl mx-auto space-y-4">
    <h2 className="text-lg font-bold text-blue-400">🔍 阶段 2: 仿真与验证</h2>
    {results.length>0&&(<div className="grid grid-cols-3 gap-2 text-xs"><RC l="Verible Lint" ok={verible?.violations===0} d={`违例:${verible?.violations??'?'}`}/><RC l="Verilator" ok={verilator?.errors===0} d={`Error:${verilator?.errors??'?'}`}/><RC l="Icarus 仿真" ok={icarus?.success} d={icarus?.assertions_ok?'断言通过':icarus?.reason||''}/></div>)}
    <div className="flex gap-1 bg-gray-900 rounded p-1 w-fit"><button onClick={()=>setActiveTab('sim')} className={`px-3 py-1 rounded text-xs ${activeTab==='sim'?'bg-blue-600':''}`}>📊 功能仿真</button><button onClick={()=>setActiveTab('formal')} className={`px-3 py-1 rounded text-xs ${activeTab==='formal'?'bg-blue-600':''}`}>🔷 形式化验证</button></div>
    {activeTab==='sim'?(
      <div className="space-y-3">
        <div className="border border-gray-700 rounded overflow-hidden" style={{height:'min(25vh,220px)',resize:'vertical'} as any}><Editor language="verilog" value={code} onChange={v=>{if(v){setCode(v);S('s1_code',v)}}} theme="vs-dark" options={{fontSize:11,minimap:{enabled:false},scrollBeyondLastLine:false}} height="100%"/></div>
        <div><div className="flex gap-1 mb-1"><button onClick={genTB} disabled={loadingTB||!code} className="bg-blue-600 px-2 py-0.5 rounded text-xs disabled:bg-gray-700">{loadingTB?'...':'🤖 AI 生成 TB'}</button><button onClick={()=>{setTb('');localStorage.removeItem('s1_tb')}} className="bg-red-700 px-2 py-0.5 rounded text-xs" title="清空TB以使用自动激励">✕ 清空</button></div>
        <div className="border border-gray-700 rounded overflow-hidden" style={{height:'min(35vh,320px)',resize:'vertical'} as any}><Editor language="verilog" value={tb} onChange={v=>{if(v){setTb(v);S('s1_tb',v);autosave('tb.v',v)}}} theme="vs-dark" options={{fontSize:11,minimap:{enabled:false},scrollBeyondLastLine:false}} height="100%"/></div></div>
        <div className="flex items-center gap-3">
          <button onClick={runSim} disabled={loadingSim||!code} className="bg-green-600 px-4 py-2 rounded text-sm disabled:bg-gray-700">{loadingSim?'...': tb ? '▶ 仿真' : '▶ 自动激励仿真'}</button>
          <div className="flex items-center gap-1 text-[10px] text-gray-500">
            <span>采样点:</span>
            <input type="range" min="5" max="100" value={sampleCount} onChange={e => setSampleCount(+e.target.value)} className="w-16 accent-blue-500" />
            <span className="text-gray-400 w-5">{sampleCount}</span>
          </div>
        </div>
        {icarus&&(<div className="bg-gray-950 rounded p-3 text-xs space-y-3"><div className="flex items-center gap-2"><span className={icarus.success?'text-green-400':'text-red-400'}>{icarus.success?'✅ 仿真通过':'❌ 仿真失败'}</span></div>
          {icarus.stdout&&(()=>{
            // 新格式 SIG rst_n=%d en=%d q=%d
            const sigs = { rst_n: [] as number[], en: [] as number[], q: [] as number[] }
            const sigRe = /SIG rst_n=(\d+) en=(\d+) q=\s*(\d+)/g
            let m; while ((m = sigRe.exec(icarus.stdout)) !== null) { sigs.rst_n.push(+m[1]); sigs.en.push(+m[2]); sigs.q.push(+m[3]) }
            // 兼容旧格式 V=
            if (sigs.q.length === 0) { const vm = icarus.stdout.match(/V=\s*(\d+)/g); if (vm) sigs.q = vm.map(v=>parseInt(v.replace(/V=\s*/,''))) }
            const vals = sigs.q
            if (vals.length===0) return <div className="text-gray-500">{icarus.stdout?.slice(0,300)}</div>
            return <div className="space-y-2">
              <div className="flex items-center gap-3 text-gray-400 text-[10px]"><span>{vals.length}采样点</span><span>q: {Math.min(...vals)}~{Math.max(...vals)}</span><span>{vals.length*10}ns</span></div>
              <div className="grid grid-cols-3 gap-1 text-[10px]">
                <div className="text-gray-500">rst_n: {sigs.rst_n.length>0?sigs.rst_n.map((v,i)=><span key={i} className="text-blue-400 ml-0.5">{v}</span>):'—'}</div>
                <div className="text-gray-500">en: {sigs.en.length>0?sigs.en.map((v,i)=><span key={i} className="text-green-400 ml-0.5">{v}</span>):'—'}</div>
                <div className="text-gray-500">q: {vals.map((v,i)=><span key={i} className="text-yellow-400 ml-0.5">{v}</span>)}</div>
              </div>
              <WaveformSVG values={vals} rst_n={sigs.rst_n} en={sigs.en} />
            </div>
          })()}
        </div>)}
      </div>
    ):(
      <div className="space-y-3">
        <div className="flex gap-1"><button onClick={genSVA} disabled={loadingSVA||!code} className="bg-blue-600 px-2 py-0.5 rounded text-xs disabled:bg-gray-700">{loadingSVA?'...':'🤖 AI 生成 SVA'}</button></div>
        <p className="text-xs text-gray-500">SymbiYosys 形式验证。AI 自动生成 SVA property，工具证明是否成立。</p>
        <textarea value={svaCode} onChange={e=>{setSvaCode(e.target.value);S('s2_sva',e.target.value)}} className="w-full h-20 bg-gray-900 border border-gray-700 rounded p-3 font-mono text-xs" placeholder={"assert property (@posedge clk) q <= 4'd15;"}/>
        <button onClick={runFormal} disabled={loadingFormal||!code||!svaCode} className="bg-purple-600 px-4 py-2 rounded text-sm disabled:bg-gray-700">{loadingFormal?'...':'▶ SymbiYosys BMC'}</button>
        {formalResult&&(<div className="space-y-2"><pre className="bg-gray-950 rounded p-3 text-xs text-gray-400 font-mono max-h-32 overflow-auto whitespace-pre-wrap">{formalResult}</pre><div className="flex gap-1 flex-wrap"><button onClick={()=>sendChat(`形式验证结果: ${formalResult}。帮我分析一下为什么失败/成功，如果需要修改SVA或RTL，给出具体建议。`)} className="bg-blue-600 px-3 py-1 rounded text-xs">🤖 AI 分析</button>{formalResult.includes('失败')&&<button onClick={()=>{setSvaRound(0);setSvaCode('');genSVA()}} className="bg-yellow-600 px-3 py-1 rounded text-xs">🔄 重新生成SVA</button>}</div></div>)}
        <div className="bg-gray-900 border border-gray-700 rounded" style={{resize:"vertical",overflow:"hidden",display:"flex",flexDirection:"column",height:"250px",minHeight:"150px"}}><div className="px-2 py-1 border-b border-gray-800 text-xs text-gray-400">💬 AI 助手</div>
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

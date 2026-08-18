'use client'
import './globals.css'
import { useState, useEffect, useRef } from 'react'
import dynamic from 'next/dynamic'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { installAuthFetch } from '@/lib/authFetch'
import { KNOWLEDGE, cardsForStage } from '@/lib/knowledge'
import { BADGES, getBadges } from '@/lib/badges'
import { getOps, fmtTime, type OpEntry } from '@/lib/oplog'
const FileExplorer = dynamic(() => import('@/components/FileExplorer'), { ssr: false })

const NAV = [
  { href: '/', label: '🏠 首页', key: 'home' },
  { href: '/stage1', label: '📐 阶段1: RTL设计', key: 'stage1' },
  { href: '/stage2', label: '🔍 阶段2: 仿真验证', key: 'stage2' },
  { href: '/stage3', label: '⚡ 阶段3: 芯片实现', key: 'stage3' },
  { href: '/compare', label: '📊 对比实验', key: 'compare' },
]

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const [showSettings, setShowSettings] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [saved, setSaved] = useState(false)
  const [rightTab, setRightTab] = useState('files')
  const [wsLog, setWsLog] = useState<string[]>([])
  const [globalChat, setGlobalChat] = useState<{role:'user'|'agent',text:string}[]>([])
  const [globalChatInput, setGlobalChatInput] = useState('')
  const [s1ok, setS1ok] = useState(false)
  const [badges, setBadges] = useState<Record<string, boolean>>({})
  const [ops, setOps] = useState<OpEntry[]>([])
  // 当前阶段 → 知识卡片上下文 (0 = 全部)
  const stageNum = pathname === '/stage1' ? 1 : pathname === '/stage2' ? 2 : pathname === '/stage3' ? 3 : 0

  const sendGlobalChat = async () => {
    const m = globalChatInput.trim(); if(!m) return
    setGlobalChat(prev=>[...prev,{role:'user',text:m}])
    setGlobalChatInput('')
    try {
      const r = await fetch('http://localhost:8000/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:m,session_id:'global'})})
      const d = await r.json()
      setGlobalChat(prev=>[...prev,{role:'agent',text:d.reply}])
    } catch { setGlobalChat(prev=>[...prev,{role:'agent',text:'连接失败'}]) }
  }

  const clearGlobalChat = async () => {
    setGlobalChat([])
    try { await fetch('http://localhost:8000/api/chat/clear',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:'global'})}) } catch {}
  }
  const [s2ok, setS2ok] = useState(false)
  useEffect(() => {
    // P1-4 鉴权 fetch 包装由 lib/authFetch 模块加载时自动安装 (早于所有页面 effect)
    // 通关判定必须真实: 检查步骤结果内容, 而不是"有结果就显示通过"
    const parse = (k: string): any[] => { try { return JSON.parse(localStorage.getItem(k)||'[]') } catch { return [] } }
    const s1 = parse('s1_results')
    const s2 = parse('s2_results')
    const stepPass = (arr: any[], step: string) => arr.some((s:any) => s.step===step && s.status==='done' && s.success===true)
    // 阶段1: 仿真通过 + 编译无 Error
    const s1Pass = stepPass(s1, 'icarus_sim') && s1.some((s:any)=>s.step==='verilator_lint' && s.status==='done' && (s.errors===0))
    // 阶段2: 功能仿真通过 + 形式验证通过 (没跑形式验证不算完成)
    const s2Pass = stepPass(s2, 'icarus_sim') && localStorage.getItem('s2_fr_pass')==='1'
    setS1ok(s1Pass); setS2ok(s2Pass)
    // 徽章在客户端加载 (SSR 不能读 localStorage, 否则 hydration 不匹配)
    setBadges(getBadges())
    setOps(getOps())
  }, [])
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    const ws = new WebSocket('ws://localhost:8000/ws/global')
    wsRef.current = ws
    ws.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data)
        // Agent 反馈流: 步骤事件 + 收敛循环 + 归档交付 (后端双广播到 global 频道)
        let msg = ''
        if (d.type === 'step_start') msg = `▶ ${d.step}`
        else if (d.type === 'step_done') msg = `${d.success===false?'❌':'✅'} ${d.step} ${d.duration?d.duration+'s':''}${d.status==='skipped'?' (跳过)':''}`
        else if (d.type === 'convergence_round') msg = `🔁 收敛 R${d.round}: ${d.status==='rerun'?'🛠 回溯重跑':d.status==='converged'?'✅ 收敛':'🛑 止损'}`
        else if (d.type === 'archive_ready') msg = `📦 归档交付: ${d.title}`
        else if (d.type === 'log') msg = `📄 ${d.text}`
        else msg = `· ${d.type||''} ${d.step||''}`
        setWsLog(prev => [...prev.slice(-19), msg])
      } catch {}
    }
    return () => ws.close()
  }, [])

  const saveSettings = async () => {
    await fetch('http://localhost:8000/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: 'deepseek_api_key', value: apiKey })
    })
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <html lang="zh-CN">
      <body className="h-screen flex flex-col bg-gray-950 text-gray-100 text-sm">
        {/* Top bar */}
        <header className="h-11 bg-gray-900 border-b border-gray-800 flex items-center justify-between px-4 shrink-0">
          <div className="flex items-center gap-2">
            <span className="text-blue-400 font-bold">iflow-lab</span>
            <nav className="flex gap-0.5 ml-4">
              {NAV.map(n => (
                <Link key={n.key} href={n.href} className={`px-2.5 py-1 rounded text-xs ${
                  pathname === n.href ? 'bg-blue-600' : 'text-gray-400 hover:bg-gray-800'}`}>
                  {n.label.split(':')[0]}
                </Link>
              ))}
            </nav>
          </div>
          <div className="text-xs text-gray-500 flex items-center gap-2">
            <button onClick={() => setShowSettings(!showSettings)} className="hover:text-white">⚙️ 设置</button>
          </div>
        </header>

        {/* Settings modal */}
        {showSettings && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowSettings(false)}>
            <div className="bg-gray-900 border border-gray-700 rounded-lg p-6 w-96 space-y-3" onClick={e => e.stopPropagation()}>
              <h3 className="text-sm font-bold text-gray-200">⚙️ 设置</h3>
              <div>
                <label className="text-xs text-gray-400 block mb-1">DeepSeek API Key</label>
                <input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-xs focus:outline-none focus:border-blue-500"
                  placeholder="sk-..." />
              </div>
              <button onClick={saveSettings}
                className={`w-full py-1.5 rounded text-xs font-medium ${saved ? 'bg-green-600' : 'bg-blue-600 hover:bg-blue-700'}`}>
                {saved ? '✅ 已保存' : '💾 保存'}
              </button>
              <p className="text-xs text-gray-600">Key 仅存储在服务器内存中，重启后需重新设置</p>
            </div>
          </div>
        )}

        <div className="flex flex-1 overflow-hidden">
          <main className="flex-1 overflow-auto p-4">{children}</main>

          <aside className="w-60 bg-gray-900 border-l border-gray-800 flex flex-col shrink-0 overflow-hidden">
            {/* 右侧标签栏 */}
            <div className="flex text-[10px] border-b border-gray-800 shrink-0">
              {[{k:'files',l:'📁 文件'},{k:'agent',l:'🤖 Agent'},{k:'info',l:'📖 信息'},{k:'badges',l:'🏅 徽章'}].map(t=>(
                <button key={t.k} onClick={()=>{setRightTab(t.k); if(t.k==='badges')setBadges(getBadges()); if(t.k==='info'||t.k==='badges')setOps(getOps())}} className={`flex-1 py-1.5 text-center ${rightTab===t.k?'bg-gray-800 text-white':'text-gray-500 hover:bg-gray-800/50'}`}>{t.l}</button>
              ))}
            </div>
            {rightTab==='files' && <div className="flex-1 min-h-0"><FileExplorer /></div>}
            {rightTab==='agent' && (
              <div className="flex-1 flex flex-col min-h-0">
                <div className="flex items-center justify-between px-2 py-1 border-b border-gray-800/50 text-[9px] text-gray-600 shrink-0">
                  <span>实时事件流 ({wsLog.length})</span>
                  {wsLog.length>0 && <button onClick={()=>setWsLog([])} className="hover:text-red-400">🗑</button>}
                </div>
                <div className="flex-1 overflow-y-auto p-2 space-y-1.5 text-[10px]">
                  {wsLog.length > 0 ? wsLog.map((m,i)=><div key={i} className="text-gray-500 font-mono">{m}</div>) : <p className="text-gray-600">运行任意流程后, 步骤/收敛/归档事件实时显示在这里</p>}
                </div>
                <div className="border-t border-gray-800 p-1.5">
                  <div className="flex gap-1">
                    <input value={globalChatInput} onChange={e=>setGlobalChatInput(e.target.value)} onKeyDown={e=>e.key==='Enter'&&sendGlobalChat()}
                      className="flex-1 bg-gray-800 rounded px-2 py-1 text-[10px] focus:outline-none" placeholder="问 AI..."/>
                    <button onClick={sendGlobalChat} className="bg-blue-600 px-2 py-0.5 rounded text-[10px] whitespace-nowrap">发送</button>
                    {globalChat.length>0 && <button onClick={clearGlobalChat} className="bg-gray-700 px-2 py-0.5 rounded text-[10px] whitespace-nowrap hover:bg-red-700" title="清空对话记录">🗑</button>}
                  </div>
                  {globalChat.length>0 && <div className="max-h-32 overflow-y-auto mt-1 space-y-1">{globalChat.map((m,i)=><div key={i} className={`text-[10px] ${m.role==='agent'?'text-gray-300':'text-blue-400'}`}><span className="text-gray-600">{m.role==='agent'?'🤖':'👤'}</span> {m.text.slice(0,200)}</div>)}</div>}
                </div>
              </div>
            )}
            {rightTab==='info' && (
              <div className="flex-1 overflow-y-auto p-2 space-y-2 text-xs">
                <div className="bg-gray-800/40 rounded p-2">
                  <h4 className="font-medium text-gray-300 mb-1">📊 阶段状态</h4>
                  <div className="text-gray-400">阶段1: {s1ok?'✅ 通过':'—'} | 阶段2: {s2ok?'✅ 通过':'—'}</div>
                </div>
                {/* 知识卡片 (方案 3.5 第一区): 按当前阶段上下文切换 */}
                <div className="bg-gray-800/40 rounded p-2">
                  <h4 className="font-medium text-gray-300 mb-1">💡 知识卡片 {stageNum>0 && <span className="text-gray-500">(阶段{stageNum})</span>}</h4>
                  <div className="space-y-1.5">
                    {(stageNum===0 ? KNOWLEDGE : cardsForStage(stageNum)).map(c => (
                      <details key={c.id} className="text-gray-400">
                        <summary className="cursor-pointer text-gray-300 hover:text-white">{c.title}</summary>
                        <p className="mt-1 text-[11px] leading-relaxed whitespace-pre-wrap text-gray-500">{c.body}</p>
                      </details>
                    ))}
                  </div>
                </div>
                <div className="bg-gray-800/40 rounded p-2">
                  <h4 className="font-medium text-gray-300 mb-1">📜 操作历史</h4>
                  {ops.length === 0 ? <div className="text-gray-500">暂无记录 — 运行阶段1/2/3 后自动记录</div> : (
                    <div className="space-y-1 max-h-44 overflow-y-auto">
                      {ops.slice(0, 12).map((o, i) => (
                        <div key={i} className="flex gap-1 items-start text-[10px] leading-snug">
                          <span className="text-gray-600 shrink-0 font-mono">{fmtTime(o.t)}</span>
                          <span className={`shrink-0 font-bold ${o.stage===1?'text-blue-400':o.stage===2?'text-purple-400':o.stage===3?'text-green-400':'text-yellow-400'}`}>S{o.stage}</span>
                          <span className={o.ok?'text-gray-400':'text-red-400'}>{o.text}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
            {rightTab==='badges' && (
              <div className="flex-1 overflow-y-auto p-2">
                <div className="text-[10px] text-gray-500 mb-2">通关进度: {Object.values(badges).filter(Boolean).length}/9 徽章</div>
                {[1,2,3].map(st => (
                  <div key={st} className="mb-2">
                    <div className="text-[10px] text-gray-500 mb-1">阶段{st}</div>
                    <div className="grid grid-cols-3 gap-1">
                      {BADGES.filter(b => b.stage === st).map(b => {
                        const earned = !!badges[b.id]
                        return (
                          <div key={b.id} title={b.desc} className={`rounded p-1.5 text-center ${earned ? 'bg-yellow-900/30 border border-yellow-700' : 'bg-gray-800/40 border border-gray-800'}`}>
                            <div className={`text-base ${earned ? '' : 'grayscale opacity-30'}`}>{b.icon}</div>
                            <div className={`text-[9px] mt-0.5 ${earned ? 'text-yellow-300' : 'text-gray-600'}`}>{b.tier}</div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                ))}
                <p className="text-[9px] text-gray-600 leading-relaxed">铜/银/金对应方案 6.2 能力池通过标准。悬停徽章查看达成条件。</p>
              </div>
            )}
            <div className="border-t border-gray-800 p-1.5 text-[10px] text-gray-500 shrink-0 flex items-center justify-between">
              <span className="text-green-400">🟢</span>
              <span>S1:{s1ok?'✅':'—'} S2:{s2ok?'✅':'—'} 🏅{Object.values(badges).filter(Boolean).length}/9</span>
            </div>
          </aside>
        </div>

        {/* Status bar */}
        <footer className="h-6 bg-gray-900 border-t border-gray-800 flex items-center justify-between px-4 text-xs text-gray-500 shrink-0">
          <span className="text-green-400">🟢 Agent 就绪</span>
          <span>API: localhost:8000</span>
        </footer>
      </body>
    </html>
  )
}

function RightPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-gray-800/40 rounded p-2">
      <h4 className="font-medium text-gray-300 mb-1">{title}</h4>
      {children}
    </div>
  )
}

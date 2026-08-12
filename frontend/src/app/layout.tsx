'use client'
import './globals.css'
import { useState, useEffect, useRef } from 'react'
import dynamic from 'next/dynamic'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
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
  const [s2ok, setS2ok] = useState(false)
  useEffect(() => { setS1ok(!!localStorage.getItem('s1_results')); setS2ok(!!localStorage.getItem('s2_results')) }, [])
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    const ws = new WebSocket('ws://localhost:8000/ws/global')
    wsRef.current = ws
    ws.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data)
        const msg = `${d.type==='step_start' ? '▶' : d.type==='step_done' ? (d.success===false ? '❌' : '✅') : '·'} ${d.step || ''} ${d.duration ? d.duration+'s' : ''}`
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
              {[{k:'files',l:'📁 文件'},{k:'agent',l:'🤖 Agent'},{k:'info',l:'📖 信息'}].map(t=>(
                <button key={t.k} onClick={()=>setRightTab(t.k)} className={`flex-1 py-1.5 text-center ${rightTab===t.k?'bg-gray-800 text-white':'text-gray-500 hover:bg-gray-800/50'}`}>{t.l}</button>
              ))}
            </div>
            {rightTab==='files' && <div className="flex-1 min-h-0"><FileExplorer /></div>}
            {rightTab==='agent' && (
              <div className="flex-1 flex flex-col min-h-0">
                <div className="flex-1 overflow-y-auto p-2 space-y-1.5 text-[10px]">
                  {wsLog.length > 0 ? wsLog.map((m,i)=><div key={i} className="text-gray-500 font-mono">{m}</div>) : <p className="text-gray-600">操作日志显示在这里</p>}
                </div>
                <div className="border-t border-gray-800 p-1.5">
                  <div className="flex gap-1">
                    <input value={globalChatInput} onChange={e=>setGlobalChatInput(e.target.value)} onKeyDown={e=>e.key==='Enter'&&sendGlobalChat()}
                      className="flex-1 bg-gray-800 rounded px-2 py-1 text-[10px] focus:outline-none" placeholder="问 AI..."/>
                    <button onClick={sendGlobalChat} className="bg-blue-600 px-2 py-0.5 rounded text-[10px] whitespace-nowrap">发送</button>
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
                <div className="bg-gray-800/40 rounded p-2">
                  <h4 className="font-medium text-gray-300 mb-1">📜 最近操作</h4>
                  <div className="text-gray-500">暂无记录</div>
                </div>
              </div>
            )}
            <div className="border-t border-gray-800 p-1.5 text-[10px] text-gray-500 shrink-0 flex items-center justify-between">
              <span className="text-green-400">🟢</span>
              <span>S1:{s1ok?'✅':'—'} S2:{s2ok?'✅':'—'}</span>
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

'use client'
import { useState, useEffect } from 'react'

const API = 'http://localhost:8000'

type FileInfo = { name: string; path: string; size: number; step: string; type: string; run_id?: string }

export default function FileExplorer() {
  const [files, setFiles] = useState<FileInfo[]>([])
  const [viewing, setViewing] = useState<FileInfo | null>(null)
  const [content, setContent] = useState('')
  const [editing, setEditing] = useState(false)
  const [saved, setSaved] = useState(false)

  const refresh = async () => {
    try {
      const r = await fetch(`${API}/api/files/recent`)
      const d = await r.json()
      setFiles(d.files || [])
    } catch {}
  }

  useEffect(() => { refresh(); const i = setInterval(refresh, 5000); return () => clearInterval(i) }, [])

  const view = async (f: FileInfo) => {
    setViewing(f); setEditing(false)
    try {
      const r = await fetch(`${API}/api/files/read?path=${encodeURIComponent(f.path)}`)
      const d = await r.json()
      setContent(d.content || '')
    } catch { setContent('无法读取') }
  }

  const download = (f: FileInfo) => { window.open(`${API}/api/files/download?path=${encodeURIComponent(f.path)}`) }

  const save = async () => {
    if (!viewing) return
    try {
      await fetch(`${API}/api/files/save`, { method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ path: viewing.path, content }) })
      setSaved(true); setTimeout(()=>setSaved(false), 1500)
      refresh()
    } catch {}
  }

  const formatSize = (s: number) => s > 1024 ? `${(s/1024).toFixed(1)}KB` : `${s}B`
  const extIcon = (name: string) => {
    if (name.endsWith('.v')||name.endsWith('.sv')) return '📝'
    if (name.endsWith('.vcd')||name.endsWith('.fst')) return '📈'
    if (name.endsWith('.gds')) return '🗺'
    if (name.endsWith('.def')) return '📐'
    if (name.endsWith('.sdc')) return '⏱'
    if (name.endsWith('.log')||name.endsWith('.rpt')) return '📋'
    if (name.endsWith('.json')) return '📊'
    return '📄'
  }

  return (
    <div className="flex flex-col h-full text-[11px]">
      {/* 文件列表 */}
      <div className="flex items-center justify-between px-2 py-1.5 border-b border-gray-800">
        <span className="text-gray-400 font-medium">📁 文件 ({files.length})</span>
        <button onClick={refresh} className="text-gray-500 hover:text-white text-[10px]">🔄</button>
      </div>
      <div className="flex-1 overflow-y-auto min-h-0">
        {files.length === 0 ? (
          <p className="text-gray-600 p-2 text-[10px]">执行 Flow 后文件出现在这里</p>
        ) : (
          files.map((f, i) => (
            <div key={i} className={`flex items-center gap-1 px-2 py-1 hover:bg-gray-800/50 cursor-pointer border-b border-gray-800/30 ${viewing?.path===f.path ? 'bg-blue-900/20' : ''}`}
              onClick={() => view(f)}>
              <span>{extIcon(f.name)}</span>
              <span className="flex-1 text-gray-300 truncate" title={f.name}>{f.name}</span>
              <span className="text-gray-600 text-[10px]">{formatSize(f.size)}</span>
              <button onClick={e=>{e.stopPropagation();download(f)}} className="text-blue-400 hover:text-blue-300 text-[10px]" title="下载">⬇</button>
            </div>
          ))
        )}
      </div>

      {/* 文件查看/编辑区 */}
      {viewing && (
        <div className="border-t border-gray-700 shrink-0">
          <div className="flex items-center justify-between px-2 py-1 bg-gray-800/50">
            <span className="text-gray-300 truncate flex-1 text-[10px]">{extIcon(viewing.name)} {viewing.name}</span>
            <div className="flex gap-1">
              <button onClick={() => setEditing(!editing)} className={`px-1.5 py-0.5 rounded text-[10px] ${editing ? 'bg-yellow-600' : 'bg-gray-700'}`}>{editing ? '锁定' : '✏️'}</button>
              {editing && <button onClick={save} className={`px-1.5 py-0.5 rounded text-[10px] ${saved ? 'bg-green-600' : 'bg-blue-600'}`}>{saved ? '✅' : '💾'}</button>}
              <button onClick={() => { setViewing(null); setEditing(false) }} className="text-gray-500 hover:text-white text-[10px]">✕</button>
            </div>
          </div>
          <textarea value={content} onChange={e => setContent(e.target.value)} readOnly={!editing}
            className="w-full h-32 bg-gray-950 p-2 font-mono text-[10px] text-gray-400 focus:outline-none resize-none" />
        </div>
      )}
    </div>
  )
}

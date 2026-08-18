'use client'
import { useState, useEffect, useRef } from 'react'
import { withToken } from '@/lib/authFetch'

const API = 'http://localhost:8000'

type FileInfo = { name: string; path: string; size: number; step: string; type: string; run_id?: string }

export default function FileExplorer() {
  const [files, setFiles] = useState<FileInfo[]>([])
  const [viewing, setViewing] = useState<FileInfo | null>(null)
  const [content, setContent] = useState('')
  const [editing, setEditing] = useState(false)
  const [saved, setSaved] = useState(false)
  // 多选: Ctrl+点击 切换单个, Shift+点击 范围选择
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [lastIdx, setLastIdx] = useState<number | null>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const refresh = async () => {
    try {
      const r = await fetch(`${API}/api/files/recent`)
      const d = await r.json()
      setFiles(d.files || [])
      // 清理已被删除的选中项
      setSelected(prev => {
        const valid = new Set((d.files || []).map((f: FileInfo) => f.path))
        const nx = new Set([...prev].filter(p => valid.has(p)))
        return nx.size === prev.size ? prev : nx
      })
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

  const download = (f: FileInfo) => { window.open(withToken(`${API}/api/files/download?path=${encodeURIComponent(f.path)}`)) }

  const save = async () => {
    if (!viewing) return
    try {
      await fetch(`${API}/api/files/save`, { method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ path: viewing.path, content }) })
      setSaved(true); setTimeout(()=>setSaved(false), 1500)
      refresh()
    } catch {}
  }

  // 右键菜单
  const [ctxMenu, setCtxMenu] = useState<{x:number, y:number, file:FileInfo}|null>(null)
  const [renaming, setRenaming] = useState<FileInfo|null>(null)
  const [newName, setNewName] = useState('')

  const onContextMenu = (e: React.MouseEvent, f: FileInfo) => {
    e.preventDefault()
    setCtxMenu({x: e.clientX, y: e.clientY, file: f})
  }

  const doRename = async () => {
    if (!renaming || !newName.trim()) { setRenaming(null); return }
    try {
      await fetch(`${API}/api/files/rename`, { method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ path: renaming.path, new_name: newName.trim() }) })
      refresh()
    } catch {}
    setRenaming(null)
  }

  const deleteOne = async (f: FileInfo) => {
    try {
      await fetch(`${API}/api/files/delete?path=${encodeURIComponent(f.path)}`, { method:'DELETE' })
      if (viewing?.path === f.path) { setViewing(null); setEditing(false) }
      setSelected(prev => { const nx = new Set(prev); nx.delete(f.path); return nx })
      refresh()
    } catch {}
    setCtxMenu(null)
  }

  const deleteSelected = async () => {
    const targets = [...selected]
    if (targets.length === 0) return
    if (targets.length > 1 && !window.confirm(`确定删除选中的 ${targets.length} 个文件?`)) return
    for (const p of targets) {
      try { await fetch(`${API}/api/files/delete?path=${encodeURIComponent(p)}`, { method:'DELETE' }) } catch {}
    }
    if (viewing && targets.includes(viewing.path)) { setViewing(null); setEditing(false) }
    setSelected(new Set())
    setCtxMenu(null)
    refresh()
  }

  // 文件行点击: 普通=查看; Ctrl/Cmd+点击=多选切换; Shift+点击=范围选择
  const onRowClick = (e: React.MouseEvent, f: FileInfo, idx: number) => {
    if (e.ctrlKey || e.metaKey) {
      setSelected(prev => {
        const nx = new Set(prev)
        if (nx.has(f.path)) nx.delete(f.path); else nx.add(f.path)
        return nx
      })
      setLastIdx(idx)
      return
    }
    if (e.shiftKey && lastIdx !== null) {
      const [a, b] = [Math.min(lastIdx, idx), Math.max(lastIdx, idx)]
      const nx = new Set(selected)
      for (let i = a; i <= b; i++) nx.add(files[i].path)
      setSelected(nx)
      return
    }
    setSelected(new Set())
    setLastIdx(idx)
    view(f)
  }

  // 键盘操作: Delete/Backspace 删除选中, Ctrl+A 全选, Esc 取消选择
  const onListKeyDown = (e: React.KeyboardEvent) => {
    if (renaming) return
    if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault()
      deleteSelected()
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
      e.preventDefault()
      setSelected(new Set(files.map(f => f.path)))
    } else if (e.key === 'Escape') {
      setSelected(new Set())
    }
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
        <div className="flex items-center gap-1.5">
          {selected.size > 0 && (
            <>
              <span className="text-blue-400">已选 {selected.size} 项</span>
              <button onClick={deleteSelected} className="bg-red-700 hover:bg-red-600 px-1.5 py-0.5 rounded text-[10px]" title="删除选中 (Delete 键)">🗑 删除</button>
              <button onClick={() => setSelected(new Set())} className="text-gray-500 hover:text-white text-[10px]" title="取消选择 (Esc)">✕</button>
            </>
          )}
          <button onClick={refresh} className="text-gray-500 hover:text-white text-[10px]">🔄</button>
        </div>
      </div>
      {/* tabIndex 使容器可获得键盘焦点: Delete/Ctrl+A/Esc */}
      <div ref={listRef} tabIndex={0} onKeyDown={onListKeyDown}
        className="flex-1 overflow-y-auto min-h-0 focus:outline-none focus:ring-1 focus:ring-blue-800/50">
        {files.length === 0 ? (
          <p className="text-gray-600 p-2 text-[10px]">执行 Flow 后文件出现在这里</p>
        ) : (
          files.map((f, i) => (
            <div key={f.path} className={`flex items-center gap-1 px-2 py-1 hover:bg-gray-800/50 cursor-pointer border-b border-gray-800/30 ${viewing?.path===f.path ? 'bg-blue-900/20' : ''} ${selected.has(f.path) ? 'bg-blue-900/40' : ''}`}
              onClick={(e) => onRowClick(e, f, i)} onContextMenu={(e) => onContextMenu(e, f)} title="左键查看 | Ctrl+点击多选 | Shift+点击范围选择 | 右键菜单">
              <span>{extIcon(f.name)}</span>
              {renaming?.path === f.path ? (
                <input autoFocus value={newName} onChange={e=>setNewName(e.target.value)}
                  onKeyDown={e=>{if(e.key==='Enter')doRename(); if(e.key==='Escape')setRenaming(null)}}
                  onBlur={doRename}
                  onClick={e=>e.stopPropagation()}
                  className="flex-1 bg-gray-800 rounded px-1 text-[10px] focus:outline-none" />
              ) : (
                <span className="flex-1 text-gray-300 truncate" title={f.name}>{f.name}</span>
              )}
              <span className="text-gray-600 text-[10px]">{formatSize(f.size)}</span>
              <button onClick={e=>{e.stopPropagation();download(f)}} className="text-blue-400 hover:text-blue-300 text-[10px]" title="下载">⬇</button>
              <button onClick={e=>{e.stopPropagation();deleteOne(f)}} className="text-red-500 hover:text-red-300 text-[10px]" title="删除">🗑</button>
            </div>
          ))
        )}
      </div>

      {/* 右键菜单 */}
      {ctxMenu && (
        <>
        {/* 全屏遮罩: 点击空白区域 (或右键别处) 关闭菜单 */}
        <div className="fixed inset-0 z-40"
          onClick={() => setCtxMenu(null)}
          onContextMenu={(e) => { e.preventDefault(); setCtxMenu(null) }} />
        <div className="fixed z-50 bg-gray-800 border border-gray-600 rounded shadow-lg py-1 min-w-[140px]"
          style={{left: ctxMenu.x, top: ctxMenu.y}}
          onClick={() => setCtxMenu(null)}>
          <button className="w-full text-left px-3 py-1.5 text-[11px] text-gray-300 hover:bg-gray-700"
            onClick={() => { setRenaming(ctxMenu.file); setNewName(ctxMenu.file.name); setCtxMenu(null) }}>
            ✏️ 重命名
          </button>
          <button className="w-full text-left px-3 py-1.5 text-[11px] text-gray-300 hover:bg-gray-700"
            onClick={() => { download(ctxMenu.file); setCtxMenu(null) }}>
            ⬇ 保存到本地
          </button>
          <button className="w-full text-left px-3 py-1.5 text-[11px] text-red-400 hover:bg-gray-700"
            onClick={() => deleteOne(ctxMenu.file)}>
            🗑 删除
          </button>
        </div>
        </>
      )}

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

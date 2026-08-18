'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import { withToken } from '@/lib/authFetch'

const API = 'http://localhost:8000'

/**
 * GDS 版图预览 (增强版)
 * - 缩放 (滚轮/按钮) + 拖拽平移
 * - 图层图例 (点击切换显隐)
 * - 悬停多边形高亮
 * - GDS 文件下载
 */
export default function GdsPreview({ path }: { path: string }) {
  const [svg, setSvg] = useState('')
  const [info, setInfo] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [hiddenLayers, setHiddenLayers] = useState<Set<number>>(new Set())
  const containerRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ x: number; y: number; px: number; py: number } | null>(null)

  const load = useCallback(async () => {
    if (!path) return
    setLoading(true); setError('')
    try {
      const r = await fetch(`${API}/api/gds/preview?path=${encodeURIComponent(path)}`)
      const d = await r.json()
      if (d.svg) { setSvg(d.svg); setInfo(d) }
      else setError(d.detail || '预览失败')
    } catch { setError('无法连接后端') }
    setLoading(false)
  }, [path])

  useEffect(() => { load() }, [load])

  // 图层显隐: dangerouslySetInnerHTML 的 SVG 需直接操作 DOM
  useEffect(() => {
    const el = containerRef.current
    if (!el || !svg) return
    el.querySelectorAll('.gds-layer').forEach((g: Element) => {
      const l = Number(g.getAttribute('data-layer'))
      ;(g as HTMLElement).style.display = hiddenLayers.has(l) ? 'none' : ''
    })
  }, [svg, hiddenLayers])

  // 按容器实际尺寸缩放并居中 (SVG 基准 800x600)
  const fitToContainer = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    const z = Math.min(el.clientWidth / 800, el.clientHeight / 600)
    setZoom(Math.max(0.05, z))
    setPan({ x: (el.clientWidth - 800 * z) / 2, y: (el.clientHeight - 600 * z) / 2 })
  }, [])

  const fit = fitToContainer
  const zoomBy = (f: number) => setZoom(z => Math.min(8, Math.max(0.05, +(z * f).toFixed(3))))

  // 加载完成后自动适应容器; 窗口尺寸变化时重新适应
  useEffect(() => { if (svg) fitToContainer() }, [svg, fitToContainer])
  useEffect(() => {
    window.addEventListener('resize', fitToContainer)
    return () => window.removeEventListener('resize', fitToContainer)
  }, [fitToContainer])

  // 滚轮缩放 (原生监听, React onWheel 是 passive 无法 preventDefault)
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      zoomBy(e.deltaY < 0 ? 1.3 : 1 / 1.3)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const onMouseDown = (e: React.MouseEvent) => {
    dragRef.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y }
  }
  const onMouseMove = (e: React.MouseEvent) => {
    if (!dragRef.current) return
    setPan({ x: dragRef.current.px + (e.clientX - dragRef.current.x),
             y: dragRef.current.py + (e.clientY - dragRef.current.y) })
  }
  const onMouseUp = () => { dragRef.current = null }

  const toggleLayer = (l: number) => {
    setHiddenLayers(prev => {
      const nx = new Set(prev)
      if (nx.has(l)) nx.delete(l); else nx.add(l)
      return nx
    })
  }

  return (
    <div className="bg-gray-900 border border-gray-700 rounded p-3">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <h4 className="text-xs font-medium text-gray-300">🗺 GDS 版图预览</h4>
        <div className="flex items-center gap-1.5 text-[11px]">
          {info && <span className="text-gray-500 text-[10px]">
            {info.width}×{info.height}μm · {info.cells} cells · {info.total_polygons} 多边形
            {info.format === 'gds_text' ? ' · GDS文本格式' : ''}
          </span>}
          <button onClick={() => zoomBy(1 / 1.3)} className="bg-gray-700 hover:bg-gray-600 px-1.5 py-0.5 rounded" title="缩小">−</button>
          <span className="text-gray-400 w-10 text-center">{Math.round(zoom * 100)}%</span>
          <button onClick={() => zoomBy(1.3)} className="bg-gray-700 hover:bg-gray-600 px-1.5 py-0.5 rounded" title="放大">＋</button>
          <button onClick={fit} className="bg-gray-700 hover:bg-gray-600 px-2 py-0.5 rounded" title="适应窗口">⤢ 适应</button>
          <a href={withToken(`${API}/api/files/download?path=${encodeURIComponent(path)}`)}
             className="bg-blue-700 hover:bg-blue-600 px-2 py-0.5 rounded text-gray-200" download>
            ⬇ 下载 GDS
          </a>
        </div>
      </div>

      {/* 图层图例 (点击切换显隐) */}
      {info?.layers && info.layers.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {info.layers.map((l: any) => {
            const hidden = hiddenLayers.has(l.layer)
            return (
              <button key={l.layer} onClick={() => toggleLayer(l.layer)}
                className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] border ${hidden ? 'bg-gray-800/40 border-gray-700 text-gray-600 line-through' : 'bg-gray-800 border-gray-600 text-gray-300'}`}
                title={`图层 ${l.layer}: ${l.count} 个多边形 (点击${hidden ? '显示' : '隐藏'})`}>
                <span className="w-2 h-2 rounded-sm inline-block" style={{ background: l.color }} />
                L{l.layer}
              </button>
            )
          })}
          <button onClick={() => setHiddenLayers(new Set())} className="text-[10px] text-gray-500 hover:text-gray-300 px-1">全部显示</button>
        </div>
      )}

      {/* 画布: 缩放 + 拖拽平移 */}
      <div ref={containerRef} className="bg-black rounded overflow-hidden relative select-none"
        style={{ height: 'min(55vh, 480px)', cursor: dragRef.current ? 'grabbing' : 'grab', touchAction: 'none' }}
        onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseUp={onMouseUp} onMouseLeave={onMouseUp}>
        {loading && <div className="absolute inset-0 flex items-center justify-center text-gray-500 text-xs">加载中...</div>}
        {error && <div className="absolute inset-0 flex items-center justify-center text-red-400 text-xs p-4 text-center">{error}</div>}
        {svg && !error && (
          <div style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transformOrigin: '0 0' }}
            className="absolute top-0 left-0" dangerouslySetInnerHTML={{ __html: svg }} />
        )}
        <div className="absolute bottom-1 right-2 text-[9px] text-gray-600 bg-black/60 rounded px-1.5 py-0.5">
          滚轮缩放 · 拖拽平移 · 悬停高亮
        </div>
      </div>
    </div>
  )
}

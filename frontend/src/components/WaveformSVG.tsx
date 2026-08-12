'use client'
import { useState } from 'react'

/** 从 V= 信号数据渲染 SVG 时序图 (支持 hover tooltip) */
export default function WaveformSVG({ values, rst_n, en, duration }: { values: number[]; rst_n?: number[]; en?: number[]; duration?: number }) {
  if (!values || values.length < 2) return null
  const hasRst = rst_n && rst_n.length === values.length
  const hasEn = en && en.length === values.length
  const [tooltip, setTooltip] = useState<{x:number,y:number,v:number,i:number}|null>(null)

  const width = 800, height = 200
  const margin = { top: 20, right: 30, bottom: 30, left: 60 }
  const plotW = width - margin.left - margin.right
  const plotH = height - margin.top - margin.bottom
  const samples = values.length
  const maxVal = Math.max(...values, 1)
  const midY = margin.top + plotH / 2
  const amp = plotH / 2 - 10

  // 时钟波形
  const clkPath = []; const halfCycle = plotW / samples / 2
  for (let i = 0; i < samples * 2; i++) {
    const x = margin.left + i * halfCycle
    clkPath.push(`${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${midY - amp + (i % 2 === 0 ? 0 : amp * 2)}`)
  }

  // 信号波形
  const sigPath = []
  for (let i = 0; i < samples; i++) {
    const x = margin.left + (i / (samples - 1)) * plotW
    const y = midY + amp - (values[i] / maxVal) * amp * 2
    sigPath.push(`${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`)
  }

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full bg-gray-950 rounded font-mono">
        <defs><pattern id="grid" width="40" height={plotH} patternUnits="userSpaceOnUse" x={margin.left} y={margin.top}><path d={`M 40 0 L 40 ${plotH}`} stroke="#1f2937" strokeWidth="0.5"/></pattern></defs>
        <rect x={margin.left} y={margin.top} width={plotW} height={plotH} fill="url(#grid)"/>
        <rect x={margin.left} y={margin.top} width={plotW} height={plotH} fill="none" stroke="#374151" strokeWidth="1"/>

        {/* Y 轴 */}
        {Array.from({length: maxVal + 1}, (_, v) => {
          const y = midY + amp - (v / maxVal) * amp * 2
          return <text key={v} x={margin.left - 6} y={y + 4} textAnchor="end" fill="#6b7280" fontSize="10">{v}</text>
        })}

        {/* X 轴 */}
        {[0, Math.floor(samples/2), samples-1].map(i => {
          const x = margin.left + (i / (samples - 1)) * plotW
          return <text key={i} x={x} y={margin.top + plotH + 16} textAnchor="middle" fill="#6b7280" fontSize="9">{i * 10}ns</text>
        })}

        {/* 波形 */}
        <path d={clkPath.join(' ')} fill="none" stroke="#60a5fa" strokeWidth="1.5"/>
        <path d={sigPath.join(' ')} fill="none" stroke="#34d399" strokeWidth="2"/>

        {/* rst_n 波形 */}
        {hasRst && (()=>{const path=[];for(let i=0;i<values.length;i++){const x=margin.left+(i/(values.length-1))*plotW;const y=rst_n[i]?midY+amp+15:midY+amp+30;path.push(`${i===0?'M':'L'} ${x.toFixed(1)} ${y.toFixed(1)}`)};return<><path d={path.join(' ')} fill="none" stroke="#f87171" strokeWidth="2"/>{rst_n.map((v,i)=>{const x=margin.left+(i/(values.length-1))*plotW;const y=v?midY+amp+15:midY+amp+30;return<circle key={'r'+i} cx={x} cy={y} r="2" fill="#f87171"/>})}</>})()}

        {/* en 波形 */}
        {hasEn && (()=>{const path=[];for(let i=0;i<values.length;i++){const x=margin.left+(i/(values.length-1))*plotW;const y=en[i]?midY+amp+35:midY+amp+50;path.push(`${i===0?'M':'L'} ${x.toFixed(1)} ${y.toFixed(1)}`)};return<><path d={path.join(' ')} fill="none" stroke="#fbbf24" strokeWidth="2"/>{en.map((v,i)=>{const x=margin.left+(i/(values.length-1))*plotW;const y=v?midY+amp+35:midY+amp+50;return<circle key={'e'+i} cx={x} cy={y} r="2" fill="#fbbf24"/>})}</>})()}

        {/* 采样点 (hover) */}
        {values.map((v, i) => {
          const x = margin.left + (i / (samples - 1)) * plotW
          const y = midY + amp - (v / maxVal) * amp * 2
          return (
            <g key={i} onMouseEnter={() => setTooltip({x,y,v,i})} onMouseLeave={() => setTooltip(null)} style={{cursor:'pointer'}}>
              <circle cx={x} cy={y} r={tooltip?.i===i ? 6 : 3} fill={tooltip?.i===i ? '#fbbf24' : '#34d399'} className="transition-all"/>
              {/* 不可见的宽点击区域 */}
              <rect x={x-8} y={y-8} width={16} height={16} fill="transparent"/>
            </g>
          )
        })}

        {/* 图例 */}
        <text x={margin.left} y={14} fill="#60a5fa" fontSize="10" fontWeight="bold">clk</text>
        <text x={margin.left + 30} y={14} fill="#34d399" fontSize="10" fontWeight="bold">q</text>
        {hasRst && <text x={margin.left + 50} y={14} fill="#f87171" fontSize="10" fontWeight="bold">rst_n</text>}
        {hasEn && <text x={margin.left + (hasRst?90:50)} y={14} fill="#fbbf24" fontSize="10" fontWeight="bold">en</text>}
      </svg>

      {/* Tooltip */}
      {tooltip && (
        <div className="absolute bg-gray-800 border border-gray-600 rounded px-2 py-1 text-[10px] text-gray-200 pointer-events-none z-10"
          style={{ left: `${((tooltip.x - margin.left) / plotW) * 100}%`, top: `${((tooltip.y - margin.top) / plotH) * 100}%`, transform: 'translate(-50%, -120%)' }}>
          时间: {tooltip.i * 10}ns<br/>q = {tooltip.v}
        </div>
      )}
    </div>
  )
}

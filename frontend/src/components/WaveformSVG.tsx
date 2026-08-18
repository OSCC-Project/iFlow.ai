'use client'
import { useState } from 'react'

/**
 * 数字信号时序图 (可读性增强版)
 * - 阶梯波形 + 每个采样点的值标注 (密度自适应, 避免拥挤)
 * - 复位区间 (rst_n=0) 淡红底纹, 一眼看出复位段
 * - 顶部说明采样语义: 每个采样点 = 1 个时钟周期, 在上升沿采样
 * - 时间轴 (ns) + 多信号轨道 + hover tooltip
 * - clk 仅在有真实数据时绘制
 */
export default function WaveformSVG({
  values, rst_n, en, clk, timeStep = 10,
}: {
  values: number[]; rst_n?: number[]; en?: number[]; clk?: number[]; timeStep?: number
}) {
  const [tooltip, setTooltip] = useState<{x:number,y:number,v:number,i:number}|null>(null)
  // 注意: 早退必须在所有 hooks 之后 (React hooks 规则), 否则重挂载会崩溃
  if (!values || values.length < 2) return null
  const hasRst = !!rst_n && rst_n.length === values.length
  const hasEn = !!en && en.length === values.length
  const hasClk = !!clk && clk.length === values.length

  const samples = values.length
  const maxVal = Math.max(...values, 1)

  // 轨道布局: q 占 2 轨高度, 每个数字信号占 1 轨
  const trackH = 34
  const nTracks = 2 + (hasRst ? 1 : 0) + (hasEn ? 1 : 0) + (hasClk ? 1 : 0)
  const margin = { top: 26, right: 30, bottom: 28, left: 56 }
  const plotW = 800 - margin.left - margin.right
  const plotH = nTracks * trackH + 10
  const height = margin.top + plotH + margin.bottom

  const trackY = (trackIdx: number) => margin.top + trackIdx * trackH + 6
  const trackMid = (trackIdx: number) => trackY(trackIdx) + trackH / 2 - 3

  const xAt = (i: number) => margin.left + (i / (samples - 1)) * plotW

  // 标注密度: 最多 24 个点标注, 避免小字挤成一团
  const labelStep = Math.max(1, Math.ceil(samples / 24))

  // 阶梯路径: 水平保持到下一个采样点, 垂直跳变
  const stairPath = (trackIdx: number, vals: number[], vMax: number, ampPx: number) => {
    const base = trackMid(trackIdx) + ampPx / 2
    const yOf = (v: number) => base - (v / Math.max(vMax, 1)) * ampPx
    let d = `M ${xAt(0).toFixed(1)} ${yOf(vals[0]).toFixed(1)}`
    for (let i = 1; i < vals.length; i++) {
      const x = xAt(i)
      d += ` L ${x.toFixed(1)} ${yOf(vals[i-1]).toFixed(1)}`  // 水平保持
      d += ` L ${x.toFixed(1)} ${yOf(vals[i]).toFixed(1)}`    // 垂直跳变
    }
    d += ` L ${(margin.left + plotW).toFixed(1)} ${yOf(vals[vals.length-1]).toFixed(1)}`
    return d
  }

  const binaryY = (trackIdx: number, v: number) => {
    // 二进制: 高=轨顶, 低=轨底
    return v ? trackY(trackIdx) + 5 : trackY(trackIdx) + trackH - 9
  }
  const stairBinary = (trackIdx: number, vals: number[]) => {
    let d = `M ${xAt(0).toFixed(1)} ${binaryY(trackIdx, vals[0]).toFixed(1)}`
    for (let i = 1; i < vals.length; i++) {
      const x = xAt(i)
      d += ` L ${x.toFixed(1)} ${binaryY(trackIdx, vals[i-1]).toFixed(1)}`
      d += ` L ${x.toFixed(1)} ${binaryY(trackIdx, vals[i]).toFixed(1)}`
    }
    d += ` L ${(margin.left + plotW).toFixed(1)} ${binaryY(trackIdx, vals[vals.length-1]).toFixed(1)}`
    return d
  }

  // 轨道分配
  let track = 0
  const qTrack = track++
  const clkTrack = hasClk ? track++ : -1
  const rstTrack = hasRst ? track++ : -1
  const enTrack = hasEn ? track++ : -1

  // 复位区间: rst_n=0 的连续段 → 淡红底纹
  const resetRects: {x1:number, x2:number}[] = []
  if (hasRst) {
    let start = -1
    for (let i = 0; i <= samples; i++) {
      const low = i < samples && rst_n![i] === 0
      if (low && start < 0) start = i
      if (!low && start >= 0) {
        resetRects.push({x1: xAt(Math.max(start, 0)), x2: xAt(Math.min(i, samples-1))})
        start = -1
      }
    }
  }

  // X 轴刻度: 每 labelStep 个点一个时间标签
  const xTicks: number[] = []
  for (let i = 0; i < samples; i += labelStep) xTicks.push(i)
  if (xTicks[xTicks.length-1] !== samples-1) xTicks.push(samples-1)

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${800} ${height}`} className="w-full bg-gray-950 rounded font-mono">
        {/* 顶部采样语义说明 */}
        <text x={margin.left} y={14} fill="#9ca3af" fontSize="9">
          ⏱ 每个采样点 = 1 个时钟周期 ({timeStep}ns), 在 clk 上升沿采样 · 鼠标悬停查看任意点
        </text>

        {/* 复位区间底纹 */}
        {resetRects.map((r, i) => (
          <rect key={i} x={r.x1} y={margin.top - 4} width={Math.max(r.x2 - r.x1, 2)} height={plotH + 6}
            fill="#ef4444" fillOpacity="0.07" />
        ))}

        {/* 网格 */}
        {Array.from({length: nTracks}, (_, t) => (
          <line key={t} x1={margin.left} y1={trackY(t) + trackH - 4} x2={margin.left+plotW} y2={trackY(t) + trackH - 4} stroke="#1f2937" strokeWidth="0.5"/>
        ))}
        <rect x={margin.left} y={margin.top} width={plotW} height={plotH} fill="none" stroke="#374151" strokeWidth="1"/>

        {/* q 轨道: 阶梯波形 + 值标注 */}
        <path d={stairPath(qTrack, values, maxVal, trackH - 14)} fill="none" stroke="#34d399" strokeWidth="2"/>
        {values.map((v, i) => {
          if (i % labelStep !== 0 && i !== samples-1) return null
          const x = xAt(i)
          const y = trackMid(qTrack) + (trackH-14)/2 - (v / maxVal) * (trackH-14)
          return (
            <g key={i} onMouseEnter={() => setTooltip({x,y,v,i})} onMouseLeave={() => setTooltip(null)} style={{cursor:'pointer'}}>
              <circle cx={x} cy={y} r={tooltip?.i===i ? 6 : 3} fill={tooltip?.i===i ? '#fbbf24' : '#34d399'} className="transition-all"/>
              <text x={x} y={y - 6} textAnchor="middle" fill="#a7f3d0" fontSize="8">{v}</text>
              <rect x={x-9} y={y-14} width={18} height={20} fill="transparent"/>
            </g>
          )
        })}

        {/* clk (仅真实数据) */}
        {hasClk && <path d={stairBinary(clkTrack, clk!)} fill="none" stroke="#60a5fa" strokeWidth="1.5"/>}

        {/* rst_n */}
        {hasRst && <path d={stairBinary(rstTrack, rst_n!)} fill="none" stroke="#f87171" strokeWidth="2"/>}

        {/* en */}
        {hasEn && <path d={stairBinary(enTrack, en!)} fill="none" stroke="#fbbf24" strokeWidth="2"/>}

        {/* 二进制轨道的 0/1 值标注 */}
        {hasRst && rst_n!.map((v, i) => {
          if (i % labelStep !== 0 && i !== samples-1) return null
          return <text key={i} x={xAt(i)} y={binaryY(rstTrack, v) + (v ? -5 : 12)} textAnchor="middle" fill="#fca5a5" fontSize="8">{v}</text>
        })}
        {hasEn && en!.map((v, i) => {
          if (i % labelStep !== 0 && i !== samples-1) return null
          return <text key={i} x={xAt(i)} y={binaryY(enTrack, v) + (v ? -5 : 12)} textAnchor="middle" fill="#fcd34d" fontSize="8">{v}</text>
        })}

        {/* 时间轴 */}
        {xTicks.map(i => (
          <g key={i}>
            <line x1={xAt(i)} y1={margin.top+plotH} x2={xAt(i)} y2={margin.top+plotH+4} stroke="#4b5563" strokeWidth="0.5"/>
            <text x={xAt(i)} y={margin.top + plotH + 17} textAnchor="middle" fill="#6b7280" fontSize="8">
              {i * timeStep}ns
            </text>
          </g>
        ))}

        {/* 信号名 (左) */}
        <text x={4} y={trackMid(qTrack)+4} fill="#34d399" fontSize="9" fontWeight="bold">q</text>
        {hasClk && <text x={4} y={trackMid(clkTrack)+4} fill="#60a5fa" fontSize="9">clk</text>}
        {hasRst && <text x={4} y={trackMid(rstTrack)+4} fill="#f87171" fontSize="9">rst_n</text>}
        {hasEn && <text x={4} y={trackMid(enTrack)+4} fill="#fbbf24" fontSize="9">en</text>}
      </svg>

      {/* Tooltip */}
      {tooltip && (
        <div className="absolute bg-gray-800 border border-gray-600 rounded px-2 py-1 text-[10px] text-gray-200 pointer-events-none z-10"
          style={{ left: `${((tooltip.x - margin.left) / plotW) * 100}%`, top: `${((tooltip.y - margin.top) / plotH) * 100}%`, transform: 'translate(-50%, -120%)' }}>
          第 {tooltip.i+1} 个采样点 · {tooltip.i * timeStep}ns<br/>q = {tooltip.v}
        </div>
      )}
    </div>
  )
}

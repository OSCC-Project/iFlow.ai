'use client'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'

// P2-2: 收敛历史 — 方案 3.4 要求 WNS/面积/DRC 多轮趋势 (Recharts)
// 后端 /api/runs/history 已聚合 metrics.wns/area/power/drc
interface HistoryItem {
  run_id: string
  time: number
  metrics: { wns: number | null; area: number | null; power: number | null; drc: number | null }
  steps_done: number
  steps_failed: number
}

export default function ConvergenceChart({ history }: { history: HistoryItem[] }) {
  const data = history.map((h, i) => ({
    round: i + 1,
    wns: h.metrics?.wns ?? null,
    area: h.metrics?.area ?? null,
    drc: h.metrics?.drc ?? null,
    failed: h.steps_failed,
  }))
  const hasWns = data.some(d => d.wns !== null)
  const hasArea = data.some(d => d.area !== null)
  const hasDrc = data.some(d => d.drc !== null)

  return (
    <div className="w-full h-40 bg-gray-950 rounded">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: -14 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="round" stroke="#6b7280" fontSize={9} tickLine={false} />
          <YAxis yAxisId="left" stroke="#6b7280" fontSize={9} tickLine={false} />
          <YAxis yAxisId="right" orientation="right" stroke="#6b7280" fontSize={9} tickLine={false} />
          <Tooltip
            contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 11, borderRadius: 4 }}
            labelFormatter={(v) => `第 ${v} 轮`}
          />
          <Legend wrapperStyle={{ fontSize: 10 }} />
          {hasWns && (
            <Line yAxisId="left" type="monotone" dataKey="wns" name="WNS (ns)"
              stroke="#60a5fa" strokeWidth={1.5} dot={{ r: 2.5 }} connectNulls />
          )}
          {hasArea && (
            <Line yAxisId="right" type="monotone" dataKey="area" name="面积 (μm²)"
              stroke="#34d399" strokeWidth={1.5} dot={{ r: 2.5 }} connectNulls />
          )}
          {hasDrc && (
            <Line yAxisId="right" type="monotone" dataKey="drc" name="DRC 违规"
              stroke="#f87171" strokeWidth={1.5} dot={{ r: 2.5 }} connectNulls />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

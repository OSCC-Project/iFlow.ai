'use client'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts'

interface ChartDatum { name: string; wns: number | null; area: number | null; drc: number | null }

const METRICS = {
  area: { label: '面积 (μm²)', color: '#34d399' },
  wns: { label: 'WNS (ns)', color: '#60a5fa' },
  drc: { label: 'DRC 违规数', color: '#f87171' },
} as const

export default function CompareChart({ data, metric }: { data: ChartDatum[]; metric: keyof typeof METRICS }) {
  const m = METRICS[metric]
  return (
    <div className="w-full h-48 bg-gray-950 rounded">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="name" stroke="#6b7280" fontSize={9} tickLine={false} />
          <YAxis stroke="#6b7280" fontSize={9} tickLine={false} />
          <Tooltip
            cursor={{ fill: '#1f293755' }}
            contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 11, borderRadius: 4 }}
          />
          <Bar dataKey={metric} name={m.label} radius={[3, 3, 0, 0]}>
            {data.map((d, i) => (
              <Cell key={i} fill={d[metric] === null ? '#374151' : m.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

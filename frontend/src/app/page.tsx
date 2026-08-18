'use client'
import Link from 'next/link'
import { useEffect, useState } from 'react'

const API = 'http://localhost:8000'

export default function Home() {
  const [health, setHealth] = useState<any>(null)
  const [flows, setFlows] = useState<any[]>([])

  useEffect(() => {
    fetch(`${API}/api/health`).then(r => r.json()).then(setHealth).catch(() => {})
    fetch(`${API}/api/flows`).then(r => r.json()).then(d => setFlows(Array.isArray(d) ? d : [])).catch(() => {})
  }, [])

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <h2 className="text-lg font-bold text-blue-400">iflow-lab 集成电路 AI 实训平台</h2>

      {/* Stage cards */}
      <div className="grid grid-cols-3 gap-4">
        <StageCard href="/stage1" icon="📐" title="阶段 1" subtitle="RTL 设计与生成"
          desc="AI 生成 / 手写 / 上传 RTL → 编译 → 仿真 → 波形" />
        <StageCard href="/stage2" icon="🔍" title="阶段 2" subtitle="仿真与验证"
          desc="Debug / 功能仿真 / 覆盖率 / 形式化验证" />
        <StageCard href="/stage3" icon="⚡" title="阶段 3" subtitle="芯片实现"
          desc="综合 → 物理设计 → STA → DRC → GDS" />
      </div>

      {/* Tool status */}
      <div className="bg-gray-900 border border-gray-700 rounded p-4">
        <h4 className="text-sm font-medium text-gray-300 mb-3">🛠 EDA 工具状态</h4>
        {health ? (
          <div className="grid grid-cols-3 gap-2 text-xs">
            {Object.entries(health.tools || {}).map(([name, info]: [string, any]) => (
              <div key={name} className="bg-gray-800/50 rounded p-2 flex items-center gap-2">
                <span className="text-green-400">●</span>
                <span className="text-gray-300">{name}</span>
                <span className="text-gray-600 truncate flex-1 text-right">{String(info).split('\n')[0].slice(0, 20)}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-gray-500">后端未连接</p>
        )}
      </div>

      {/* Quick links */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-gray-900 border border-gray-700 rounded p-4">
          <h4 className="text-sm font-medium text-gray-300 mb-2">📊 最近 Flow</h4>
          {flows.length > 0 ? (
            <div className="space-y-1">
              {flows.slice(-5).reverse().map((f: any) => (
                <div key={f.flow_id} className="flex justify-between text-xs text-gray-400">
                  <span>{f.flow_id}</span>
                  <span>{f.scene}</span>
                  <span>{f.status}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-gray-500">暂无记录，去阶段页创建第一个 Flow</p>
          )}
        </div>
        <div className="bg-gray-900 border border-gray-700 rounded p-4">
          <h4 className="text-sm font-medium text-gray-300 mb-2">🚀 快速开始</h4>
          <div className="space-y-2 text-xs text-gray-400">
            <p>1. 在 <Link href="/stage1" className="text-blue-400">阶段 1</Link> 生成或上传 RTL</p>
            <p>2. 在 <Link href="/stage2" className="text-blue-400">阶段 2</Link> 做仿真验证</p>
            <p>3. 在 <Link href="/stage3" className="text-blue-400">阶段 3</Link> 跑物理实现</p>
            <p>4. 在 <Link href="/compare" className="text-blue-400">对比实验</Link> 比较多组结果</p>
          </div>
        </div>
      </div>
    </div>
  )
}

function StageCard({ href, icon, title, subtitle, desc }: {
  href: string; icon: string; title: string; subtitle: string; desc: string
}) {
  return (
    <Link href={href} className="bg-gray-900 border border-gray-700 hover:border-blue-500 rounded-lg p-4 transition-colors">
      <div className="text-2xl mb-2">{icon}</div>
      <h3 className="text-sm font-bold text-gray-200">{title}</h3>
      <p className="text-xs text-blue-400 mb-1">{subtitle}</p>
      <p className="text-xs text-gray-500">{desc}</p>
    </Link>
  )
}

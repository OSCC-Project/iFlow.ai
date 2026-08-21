'use client'
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { withToken } from '@/lib/authFetch'

const API = 'http://localhost:8000'

export default function Home() {
  const router = useRouter()
  const [health, setHealth] = useState<any>(null)
  const [exps, setExps] = useState<any[]>([])

  useEffect(() => {
    fetch(`${API}/api/health`).then(r => r.json()).then(setHealth).catch(() => {})
    // 实验历史需要鉴权 (带 token); 后端启动时从磁盘恢复, 历史不随重启丢失
    fetch(withToken(`${API}/api/experiments`)).then(r => r.json())
      .then(d => setExps(Array.isArray(d) ? d : [])).catch(() => {})
  }, [])

  // 点击历史实验 → 标记为当前实验并跳转对比页 (对比页挂载时按 ID 恢复结果)
  const openExp = (id: string) => {
    localStorage.setItem('cmp_exp_id', id)
    router.push('/compare')
  }

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
          <h4 className="text-sm font-medium text-gray-300 mb-2">📊 对比实验历史 <span className="text-[10px] text-gray-600">(点击恢复)</span></h4>
          {exps.length > 0 ? (
            <div className="space-y-1">
              {exps.slice(-5).reverse().map((e: any) => (
                <button key={e.id} onClick={() => openExp(e.id)} title="打开该实验的结果"
                  className="w-full flex justify-between items-center text-xs text-gray-400 hover:bg-gray-800/60 rounded px-1.5 py-1 text-left">
                  <span className="font-mono">{e.id}</span>
                  <span className="text-gray-500">{e.design}</span>
                  <span>{e.completed || 0}/{e.total || 0} 组合</span>
                  <span className={e.status === 'done' ? 'text-green-400' : 'text-yellow-500'}>
                    {e.status === 'done' ? '完成' : e.status}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <p className="text-xs text-gray-500">暂无实验，去 <Link href="/compare" className="text-blue-400">对比实验</Link> 创建第一个</p>
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

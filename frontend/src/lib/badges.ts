// 通关徽章 (方案 3.5 右栏第二区): 3 阶段 × 铜/银/金 = 9 枚
// 判定标准对齐方案 6.2 能力池的通过标准:
// 阶段1 (RTL-001): 铜=编译通过 / 银=仿真波形匹配预期 / 金=交叉验证100%+Lint通过
// 阶段2 (RTL-006/QA-006): 铜=功能仿真完成 / 银=波形正确 / 金=形式验证通过
// 阶段3 (SYN-004/PV-007/IMP-002): 铜=PPA数据 / 银=版图生成 / 金=时序+DRC clean
import { addOp } from './oplog'

export type BadgeId = 's1_bronze'|'s1_silver'|'s1_gold'|'s2_bronze'|'s2_silver'|'s2_gold'|'s3_bronze'|'s3_silver'|'s3_gold'

export const BADGES: { id: BadgeId; stage: 1|2|3; tier: '铜'|'银'|'金'; icon: string; desc: string }[] = [
  { id: 's1_bronze', stage: 1, tier: '铜', icon: '🥉', desc: 'RTL 编译通过' },
  { id: 's1_silver', stage: 1, tier: '银', icon: '🥈', desc: '仿真波形匹配预期' },
  { id: 's1_gold',   stage: 1, tier: '金', icon: '🥇', desc: '交叉验证 100% + Lint 通过' },
  { id: 's2_bronze', stage: 2, tier: '铜', icon: '🥉', desc: '功能仿真完成' },
  { id: 's2_silver', stage: 2, tier: '银', icon: '🥈', desc: '波形正确 (匹配率 100%)' },
  { id: 's2_gold',   stage: 2, tier: '金', icon: '🥇', desc: '形式验证 BMC 通过' },
  { id: 's3_bronze', stage: 3, tier: '铜', icon: '🥉', desc: '拿到综合后 PPA 数据' },
  { id: 's3_silver', stage: 3, tier: '银', icon: '🥈', desc: '生成 GDS 版图' },
  { id: 's3_gold',   stage: 3, tier: '金', icon: '🥇', desc: '时序 WNS≥0 + DRC=0' },
]

const KEY = 'iflow_badges'

export function getBadges(): Record<string, boolean> {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') } catch { return {} }
}

export function awardBadge(id: BadgeId) {
  const b = getBadges()
  if (!b[id]) {
    b[id] = true
    localStorage.setItem(KEY, JSON.stringify(b))
    // 徽章解锁自动记入操作历史 (右栏第四区)
    const meta = BADGES.find(x => x.id === id)
    if (meta) addOp(meta.stage, `🏅 解锁徽章 [${meta.tier}] ${meta.desc}`)
  }
}

export function badgeCount(): number {
  return Object.values(getBadges()).filter(Boolean).length
}

const matchRate = (reason?: string): number => {
  const m = (reason || '').match(/匹配率\s*(\d+)%/)
  return m ? parseInt(m[1]) : 0
}

// 阶段1 判定 (输入: /api/flow/run 的 results)
export function awardStage1(results: any[]) {
  const verilator = results?.find((s: any) => s.step === 'verilator_lint')
  const icarus = results?.find((s: any) => s.step === 'icarus_sim')
  const verible = results?.find((s: any) => s.step === 'verible_lint')
  if (verilator?.status === 'done' && verilator?.errors === 0) awardBadge('s1_bronze')
  if (icarus?.success && matchRate(icarus.reason) > 0) awardBadge('s1_silver')
  if (icarus?.success && matchRate(icarus.reason) === 100 && (verible?.violations ?? 1) === 0)
    awardBadge('s1_gold')
}

// 阶段2 判定 (输入: 自动激励仿真的 icarus step)
export function awardStage2(icarus: any) {
  if (icarus?.success) {
    awardBadge('s2_bronze')
    if (icarus.assertions_ok || matchRate(icarus.reason) === 100) awardBadge('s2_silver')
  }
  if (localStorage.getItem('s2_fr_pass') === '1') awardBadge('s2_gold')
}

// 阶段3 判定 (输入: /api/flow/run 的 results)
export function awardStage3(results: any[]) {
  const sta = results?.find((s: any) => s.step === 'ista_sta')
  const gds = results?.find((s: any) => s.step === 'gds_export')
  const drc = results?.find((s: any) => s.step === 'idrc_drc')
  if (sta?.status === 'done' && sta.metrics?.wns !== null && sta.metrics?.wns !== undefined)
    awardBadge('s3_bronze')
  if (gds?.success && gds.gds_path) awardBadge('s3_silver')
  if (sta?.metrics?.wns !== null && sta?.metrics?.wns !== undefined
      && sta.metrics.wns >= 0 && drc?.metrics?.drc === 0)
    awardBadge('s3_gold')
}

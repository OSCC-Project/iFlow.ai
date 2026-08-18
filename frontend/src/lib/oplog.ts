// 操作历史 (方案 3.5 右栏第四区): 各阶段关键操作客户端持久化
export type OpEntry = { t: number; stage: number; text: string; ok: boolean }
const KEY = 'iflow_op_history'
const MAX = 50

export function addOp(stage: number, text: string, ok = true) {
  try {
    const list: OpEntry[] = JSON.parse(localStorage.getItem(KEY) || '[]')
    // 连续重复去重 (如重复点击同一按钮)
    if (list[0] && list[0].text === text) {
      list[0].t = Date.now()
      localStorage.setItem(KEY, JSON.stringify(list))
      return
    }
    list.unshift({ t: Date.now(), stage, text, ok })
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)))
  } catch {}
}

export function getOps(): OpEntry[] {
  try { return JSON.parse(localStorage.getItem(KEY) || '[]') } catch { return [] }
}

export function clearOps() {
  localStorage.removeItem(KEY)
}

export function fmtTime(t: number): string {
  const d = new Date(t)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

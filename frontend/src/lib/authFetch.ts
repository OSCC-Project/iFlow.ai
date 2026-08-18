// P1-4: 全局 fetch 包装 — 自动注册/登录匿名用户并注入 Authorization header
// 方案 F2 用户隔离的最小实现: 每个浏览器一个匿名账号 (stu_xxx), token 存 localStorage
export const API_BASE = 'http://localhost:8000'

let loginPromise: Promise<string> | null = null

function getToken(): Promise<string> {
  if (typeof window === 'undefined') return Promise.resolve('')
  const cached = localStorage.getItem('iflow_token')
  if (cached) return Promise.resolve(cached)
  if (!loginPromise) {
    loginPromise = (async () => {
      try {
        // 尝试 1: 已有账号
        let username = localStorage.getItem('iflow_user')
        if (username) {
          const r2 = await fetch(`${API_BASE}/api/auth/login`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password: 'iflow' }),
          })
          const d = await r2.json()
          if (d.token) {
            localStorage.setItem('iflow_token', d.token)
            return d.token
          }
        }
        // 尝试 2: 新匿名账号 (注册可能 400 已存在 → 换名重试一次)
        for (let i = 0; i < 2; i++) {
          username = 'stu_' + Math.random().toString(36).slice(2, 10)
          localStorage.setItem('iflow_user', username)
          const r1 = await fetch(`${API_BASE}/api/auth/register`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password: 'iflow', role: 'student' }),
          })
          if (!r1.ok && r1.status !== 400) throw new Error('register failed')
          const r2 = await fetch(`${API_BASE}/api/auth/login`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password: 'iflow' }),
          })
          const d = await r2.json()
          if (d.token) {
            localStorage.setItem('iflow_token', d.token)
            return d.token
          }
        }
        return ''
      } catch {
        return ''  // 后端不可达时降级为无 token (后端此时也会拒绝, 不影响体验)
      }
    })()
  }
  return loginPromise
}

// 下载链接 (<a href>/window.open 导航请求不经过 fetch 包装, 无法携带 Authorization 头)
// → 把 token 拼到 query 参数, 后端中间件支持 ?token= 校验
export function withToken(url: string): string {
  if (typeof window === 'undefined') return url
  const t = localStorage.getItem('iflow_token')
  if (!t) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}token=${encodeURIComponent(t)}`
}

export function installAuthFetch() {
  if (typeof window === 'undefined') return
  if ((window as any).__iflowAuthFetchInstalled) return
  ;(window as any).__iflowAuthFetchInstalled = true
  const orig = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    // 仅对后端 API 注入 (跳过注册/登录端点自身, 避免递归)
    if (url && url.includes(`${API_BASE}/api/`) && !url.includes('/api/auth/')) {
      const headers = init.headers as Record<string, string> | undefined
      if (!headers || !Object.keys(headers).some(k => k.toLowerCase() === 'authorization')) {
        const token = await getToken()
        if (token) {
          init.headers = { ...(init.headers || {}), Authorization: `Bearer ${token}` }
        }
      }
    }
    return orig(input, init)
  }
}

// 模块加载即安装 (不能放在 useEffect: React effect 子组件先于父组件执行,
// 首页等页面会在包装安装前发出裸 fetch → 401)
installAuthFetch()

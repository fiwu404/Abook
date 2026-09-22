export let token = localStorage.getItem('abook-token') || ''

export class ApiNetworkError extends Error {
  constructor() {
    super('无法连接服务器，请检查后端服务和前端代理后重试')
    this.name = 'ApiNetworkError'
  }
}

export function setToken(value: string) {
  token = value
  if (value) localStorage.setItem('abook-token', value)
  else localStorage.removeItem('abook-token')
}

export async function api(path: string, method = 'GET', body?: unknown): Promise<any> {
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json'
  let response: Response
  try {
    response = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body) })
  } catch {
    throw new ApiNetworkError()
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data))
  return data
}

export async function apiBlob(path: string): Promise<Blob> {
  const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
  let response: Response
  try { response = await fetch(`/api${path}`, { headers }) }
  catch { throw new ApiNetworkError() }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(typeof data.detail === 'string' ? data.detail : '图片加载失败')
  }
  return response.blob()
}

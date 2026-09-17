export let token = localStorage.getItem('abook-token') || ''
export function setToken(value: string) {
  token = value
  if (value) localStorage.setItem('abook-token', value)
  else localStorage.removeItem('abook-token')
}

export async function api(path: string, method = 'GET', body?: unknown): Promise<any> {
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json'
  const response = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body) })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data))
  return data
}

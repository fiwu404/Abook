<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, ApiNetworkError, apiBlob, setToken, token } from './api'

const user = ref<any>(null)
const auth = ref({ username: '', password: '' })
const accounts = ref<any[]>([])
const permissionCatalog = ref<any[]>([])
const newAccount = ref({ username: '', password: '', role: 'student', permissions: ['books.view', 'papers.view', 'exams.take', 'results.view', 'practice.use'], must_change_password: true })
const editingAccount = ref<any>(null)
const editingPermissions = ref<string[]>([])
const editingUsername = ref('')
const resetPassword = ref('')
const resetPasswordConfirm = ref('')
const resetMustChange = ref(true)
const passwordChange = ref({ current_password: '', new_password: '', confirm_password: '' })
const aiStatus = ref<any>(null)
const providers = ref<any[]>([])
const aiConfig = ref<any>({ text_provider_id: null, text_model: '', vision_provider_id: null, vision_model: '' })
const providerModels = ref<Record<number, string[]>>({})
const providerLoading = ref<Record<number, boolean>>({})
const newProvider = ref({ display_name: '', base_url: '', api_style: 'openai', api_key: '',
  ollama_scheme: 'http', ollama_host: 'host.docker.internal', ollama_port: 11434 })
const editingProviderId = ref<number | null>(null)
const providerConnectionTest = ref<any>(null)
const modelTest = ref<Record<string, any>>({})
const error = ref('')
const notice = ref('')
const busy = ref(false)
const tab = ref('overview')
const books = ref<any[]>([])
const bookId = ref<number | null>(null)
const pages = ref<any[]>([])
const catalog = ref<any>(null)
const selectedChapter = ref('')
const selectedPage = ref<any>(null)
const tocDraft = ref<any[]>([])
const learning = ref<any>(null)
const knowledge = ref<any[]>([])
const questions = ref<any[]>([])
const jobs = ref<any[]>([])
const sourceImages = ref<Record<string, string>>({})
const sourceImageErrors = ref<Record<string, string>>({})
const pendingSourceImages = new Set<string>()
const papers = ref<any[]>([])
const attempts = ref<any[]>([])
const mastery = ref<Record<string, any>>({})
const variantRequests = ref<any[]>([])
const currentAttempt = ref<any>(null)
const result = ref<any>(null)
const practice = ref<any>(null)
const remaining = ref('')

const uploadTitle = ref('')
const uploadFile = ref<File | null>(null)
const uploadTocPages = ref('')
const uploadManualToc = ref('')
const chapterQuestionCount = ref(1)
const selectedHeadingIndices = ref<number[]>([])
const knowledgeEdit = ref<number | null>(null)
const knowledgeForm = ref<any>({ title: '', content: '', chunk_id: null, source_quote: '', chapter: '' })
const questionEdit = ref<number | null>(null)
const questionForm = ref<any>({ knowledge_id: 0, stem: '', options: { A: '', B: '', C: '', D: '' }, answer: 'A', explanation: '', evidence: '', difficulty: 'medium', variant_of: null })
const paperForm = ref<any>({ title: '', question_ids: [], score_each: 10, difficulty: 'any' })
const publishStart = ref('')
const publishEnd = ref('')
const paperTimeEdit = ref<number | null>(null)
const paperTimeForm = ref({ starts_at: '', ends_at: '' })
const paperTimeOriginal = ref({ starts_at: '', ends_at: '', display_start: '', display_end: '' })

const selectedBook = computed(() => books.value.find(b => b.id === bookId.value))
function hasPermission(code: string) { return user.value?.role === 'admin' || !!user.value?.permissions?.includes(code) }
const canViewBooks = computed(() => hasPermission('books.view'))
const canManageBooks = computed(() => hasPermission('books.manage'))
const canViewKnowledge = computed(() => hasPermission('knowledge.view'))
const canEditKnowledge = computed(() => hasPermission('knowledge.edit'))
const canReviewKnowledge = computed(() => hasPermission('knowledge.review'))
const canViewQuestions = computed(() => hasPermission('questions.view'))
const canEditQuestions = computed(() => hasPermission('questions.edit'))
const canReviewQuestions = computed(() => hasPermission('questions.review'))
const canViewPapers = computed(() => hasPermission('papers.view'))
const canManagePapers = computed(() => hasPermission('papers.manage'))
const canViewJobs = computed(() => hasPermission('jobs.view'))
const canManageJobs = computed(() => hasPermission('jobs.manage'))
const canViewModels = computed(() => hasPermission('models.view'))
const canManageModels = computed(() => hasPermission('models.manage'))
const canViewUsers = computed(() => hasPermission('users.view'))
const canManageUsers = computed(() => hasPermission('users.manage'))
const canExam = computed(() => hasPermission('exams.take'))
const canResults = computed(() => hasPermission('results.view'))
const canPractice = computed(() => hasPermission('practice.use'))
const isStaff = computed(() => canManageBooks.value || canViewKnowledge.value || canViewQuestions.value || canManagePapers.value || canViewJobs.value || canViewModels.value || canViewUsers.value)
const approvedKnowledge = computed(() => knowledge.value.filter(k => k.status === 'approved' && k.chunk_id))
const chapterNames = computed<string[]>(() => (catalog.value?.chapters || []).map((item: any) => item.title))
const tocDirty = computed(() => JSON.stringify(tocDraft.value) !== JSON.stringify(catalog.value?.toc || []))
const chapterPages = computed(() => pages.value.filter(p => p.chapter === selectedChapter.value))
const chapterSections = computed(() => (learning.value?.sections || []).filter((s: any) => s.chapter === selectedChapter.value))
const chapterKnowledge = computed(() => knowledge.value.filter(k => k.chapter === selectedChapter.value))
const chapterQuestions = computed(() => questions.value.filter(q => knowledge.value.find(k => k.id === q.knowledge_id)?.chapter === selectedChapter.value))
const approvedPaperQuestions = computed(() => chapterQuestions.value.filter(q => q.status === 'approved' && knowledge.value.some(k => k.id === q.knowledge_id && k.status === 'approved')))
const paperCandidateQuestions = computed(() => approvedPaperQuestions.value.filter(q => paperForm.value.difficulty === 'any' || q.difficulty === paperForm.value.difficulty))
const approvedChapterKnowledge = computed(() => approvedKnowledge.value.filter(k => k.chapter === selectedChapter.value))
const questionSource = computed(() => approvedChapterKnowledge.value.find(k => k.id === questionForm.value.knowledge_id)?.source_quote || '')
const questionSourcePage = computed<number | null>(() => {
  const chunkId = knowledge.value.find(k => k.id === questionForm.value.knowledge_id)?.chunk_id
  return chapterSections.value.find((section: any) => section.chunk_id === chunkId)?.pdf_page || null
})
const visiblePapers = computed(() => canManagePapers.value ? papers.value : papers.value.filter(p => p.book_id === bookId.value))
const permissionGroups = computed(() => permissionCatalog.value.reduce((groups: Record<string, any[]>, item: any) => {
  ;(groups[item.group] ||= []).push(item)
  return groups
}, {}))
const chapterOutline = computed<any[]>(() => {
  const toc = catalog.value?.toc || []
  const roots = new Set((catalog.value?.chapters || []).map((item: any) => item.title))
  const start = toc.findIndex((item: any) => item.title === selectedChapter.value && roots.has(item.title))
  if (start < 0) return []
  const stop = toc.findIndex((item: any, index: number) => index > start && roots.has(item.title))
  const rootLevel = toc[start].level
  return toc.slice(start, stop < 0 ? undefined : stop).map((item: any, offset: number) => ({ ...item, index: start + offset })).filter((item: any) => item.index === start || item.level > rootLevel)
})
const headingSelectionReady = computed(() => chapterOutline.value.length > 0 && (chapterOutline.value.length === 1 || selectedHeadingIndices.value.length > 0))
const wrongRows = computed(() => result.value?.result?.filter((r: any) => !r.correct) || [])

function statusName(status: string) {
  return ({ uploaded: '已上传', toc_review: '待确认目录', toc_ready: '目录已确认', parsed: '待校对', indexed: '已索引', draft: '待审核', approved: '已通过', rejected: '未通过', needs_review: '待复核', queued: '排队中', running: '处理中', retrying: '重试中', done: '已完成', failed: '失败', cancelled: '已取消', published: '已发布', terminated: '已终止', deleted: '已删除' } as Record<string, string>)[status] || status
}
function paperEnded(paper: any) { return !!paper.ends_at && new Date(/(Z|[+-]\d{2}:\d{2})$/.test(paper.ends_at) ? paper.ends_at : `${paper.ends_at}Z`).getTime() <= Date.now() }
function paperCanDelete(paper: any) { return paper.status === 'draft' || paper.status === 'terminated' || (paper.status === 'published' && (paper.can_delete || paperEnded(paper))) }
function paperLabel(paper: any) { return paper.status === 'draft' ? '草稿' : paper.status === 'published' && (paper.can_delete || paperEnded(paper)) ? '已结束' : statusName(paper.status) }
function localDateTime(value: string) {
  const date = new Date(value.endsWith('Z') ? value : `${value}Z`)
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}
function paperPreview(paper: any): any[] {
  if (paper.snapshot?.length) return paper.snapshot
  return (paper.rule?.question_ids || []).map((id: number) => {
    const q = questions.value.find(item => item.id === id)
    return q ? { ...q, score: paper.rule.score_each } : null
  }).filter(Boolean)
}
function time(value: string) { return value ? new Date(value.endsWith('Z') ? value : `${value}Z`).toLocaleString('zh-CN') : '—' }
function flash(message: string) { notice.value = message; setTimeout(() => { if (notice.value === message) notice.value = '' }, 4500) }
async function act(task: () => Promise<any>, success = '操作完成') {
  error.value = ''; busy.value = true
  try { const value = await task(); flash(success); return value }
  catch (e: any) { error.value = e.message || String(e); return null }
  finally { busy.value = false }
}
async function login() {
  const data = await act(() => api('/auth/login', 'POST', auth.value), '登录成功')
  if (data) { setToken(data.token); user.value = data.user; if (!data.user.must_change_password) await refresh() }
}
function sourceImageKey(book: number, page: number, chunk?: number | null) { return `${book}:${page}:${chunk || 'page'}` }
function sourceImageUrl(book: number, page: number, chunk?: number | null) { return sourceImages.value[sourceImageKey(book, page, chunk)] || '' }
function sourceImageError(book: number, page: number, chunk?: number | null) { return sourceImageErrors.value[sourceImageKey(book, page, chunk)] || '' }
async function loadSourceImage(book: number, page: number, chunk?: number | null) {
  const key = sourceImageKey(book, page, chunk)
  if (sourceImages.value[key] || pendingSourceImages.has(key)) return
  pendingSourceImages.add(key)
  const requestedToken = token
  try {
    const path = chunk ? `/books/${book}/pages/${page}/chunks/${chunk}/image` : `/books/${book}/pages/${page}/image`
    const blob = await apiBlob(path)
    const url = URL.createObjectURL(blob)
    if (token !== requestedToken) { URL.revokeObjectURL(url); return }
    sourceImages.value[key] = url
    delete sourceImageErrors.value[key]
  } catch (e: any) { sourceImageErrors.value[key] = e.message || '原文页图加载失败' }
  finally { pendingSourceImages.delete(key) }
}
function clearSourceImages() {
  for (const url of Object.values(sourceImages.value)) URL.revokeObjectURL(url)
  sourceImages.value = {}; sourceImageErrors.value = {}
}
watch([tab, bookId, chapterQuestions, questionSourcePage], () => {
  if (tab.value !== 'questions' || !bookId.value) return
  for (const q of chapterQuestions.value) if (q.image_pdf_page && q.chunk_id) loadSourceImage(bookId.value, q.image_pdf_page, q.chunk_id)
  if (questionSourcePage.value) loadSourceImage(bookId.value, questionSourcePage.value)
})
watch(currentAttempt, attempt => {
  if (attempt?.book_id) for (const q of attempt.questions || []) {
    if (q.image_pdf_page && q.image_chunk_id) loadSourceImage(attempt.book_id, q.image_pdf_page, q.image_chunk_id)
  }
})
watch(result, value => {
  if (value?.book_id) for (const row of value.result || []) {
    if (row.image_pdf_page && row.image_chunk_id) loadSourceImage(value.book_id, row.image_pdf_page, row.image_chunk_id)
  }
})
watch(practice, value => {
  if (value?.question?.book_id && value.question.image_pdf_page && value.question.image_chunk_id)
    loadSourceImage(value.question.book_id, value.question.image_pdf_page, value.question.image_chunk_id)
})
function logout() { clearSourceImages(); setToken(''); user.value = null; currentAttempt.value = null; result.value = null; catalog.value = null; selectedChapter.value = ''; pages.value = []; knowledge.value = []; questions.value = [] }
function defaultPermissions(role: string) {
  return role === 'student' ? ['books.view', 'papers.view', 'exams.take', 'results.view', 'practice.use'] : ['books.view', 'papers.view']
}
watch(() => newAccount.value.role, role => { newAccount.value.permissions = defaultPermissions(role) })
async function refresh() {
  if (!user.value) return
  user.value = await api('/auth/me')
  books.value = canViewBooks.value ? await api('/books') : []
  papers.value = canViewPapers.value ? await api('/papers') : []
  jobs.value = canViewJobs.value ? await api('/jobs') : []
  if (canViewUsers.value) {
    [accounts.value, permissionCatalog.value] = await Promise.all([api('/users'), api('/permissions')])
  } else { accounts.value = []; permissionCatalog.value = [] }
  if (canViewModels.value) {
    [aiStatus.value, providers.value, aiConfig.value] = await Promise.all([api('/ai/status'), api('/providers'), api('/ai/config')])
  } else { aiStatus.value = null; providers.value = [] }
  if (canResults.value) {
    const [a, m, v] = await Promise.all([api('/my/attempts'), api('/my/mastery'), api('/my/variant-requests')])
    attempts.value = a; mastery.value = m; variantRequests.value = v
  } else { attempts.value = []; mastery.value = {}; variantRequests.value = [] }
  if (bookId.value && books.value.some(b => b.id === bookId.value)) await loadBook(bookId.value)
  else if (books.value.length) await loadBook(books.value[0].id)
  else {
    bookId.value = null; pages.value = []; catalog.value = null; tocDraft.value = []; learning.value = null
    selectedChapter.value = ''; selectedPage.value = null; selectedHeadingIndices.value = []
    knowledge.value = []; questions.value = []; knowledgeEdit.value = null; questionEdit.value = null
    paperForm.value.question_ids = []
  }
}
async function loadBook(id: number) {
  const changedBook = bookId.value !== id
  const previousToc = catalog.value?.toc
  bookId.value = id
  selectedPage.value = null
  if (canViewBooks.value) {
    const [p, k, q, c] = await Promise.all([
      canManageBooks.value ? api(`/books/${id}/pages`) : Promise.resolve([]),
      canViewKnowledge.value ? api(`/books/${id}/knowledge`) : Promise.resolve([]),
      canViewQuestions.value ? api(`/questions?book_id=${id}`) : Promise.resolve([]),
      api(`/books/${id}/catalog`),
    ])
    pages.value = p; knowledge.value = k; questions.value = q; catalog.value = c
    tocDraft.value = JSON.parse(JSON.stringify(c.toc || []))
    if (JSON.stringify(previousToc) !== JSON.stringify(c.toc)) selectedHeadingIndices.value = []
    if (changedBook || !chapterNames.value.includes(selectedChapter.value)) { selectedChapter.value = chapterNames.value[0] || ''; selectedHeadingIndices.value = [] }
    if (changedBook) { chooseKnowledge(); chooseQuestion(); paperForm.value.question_ids = [] }
    else paperForm.value.question_ids = paperForm.value.question_ids.filter((id: number) => paperCandidateQuestions.value.some(q => q.id === id))
  }
  try { learning.value = await api(`/books/${id}/learning`) } catch { learning.value = null }
  if (changedBook || !chapterNames.value.includes(selectedChapter.value)) selectedChapter.value = chapterNames.value[0] || ''
}
async function upload() {
  if (!uploadFile.value) { error.value = '请选择 PDF 文件'; return }
  const form = new FormData(); form.append('file', uploadFile.value)
  form.append('toc_pages', uploadTocPages.value)
  form.append('manual_toc', uploadManualToc.value)
  const params = new URLSearchParams({ title: uploadTitle.value })
  const book = await act(() => api(`/books?${params}`, 'POST', form), '教材已保存')
  if (book) { await refresh(); await loadBook(book.id); tab.value = 'books' }
}
async function deleteBook(book: any) {
  if (!window.confirm(`删除教材《${book.title}》并从教材库移除？已发布试卷和历史答卷会保留。`)) return
  const result = await act(() => api(`/books/${book.id}`, 'DELETE'), '教材已从教材库移除')
  if (result) await refresh()
}
async function createAccount() {
  const request = { ...newAccount.value, username: newAccount.value.username.trim() }
  if (!/^[A-Za-z0-9_]{3,40}$/.test(request.username) || request.password.length < 8) {
    error.value = '用户名须为 3-40 位字母、数字或下划线，密码至少 8 位'
    return
  }
  if (accounts.value.some(account => account.username === request.username)) { error.value = '用户名已存在'; return }
  error.value = ''; busy.value = true
  let created: any = null
  let lastError: any = null
  try {
    for (let attempt = 0; attempt < 2 && !created; attempt += 1) {
      try { created = await api('/users', 'POST', request) }
      catch (e: any) {
        lastError = e
        if (!(e instanceof ApiNetworkError)) throw e
        await new Promise(resolve => setTimeout(resolve, 500))
        try {
          const current = await api('/users')
          created = current.find((account: any) => account.username === request.username) || null
          accounts.value = current
        } catch (checkError: any) { lastError = checkError }
      }
    }
    if (!created) throw lastError || new Error('账号创建失败')
    newAccount.value = { username: '', password: '', role: 'student', permissions: defaultPermissions('student'), must_change_password: true }
    try { accounts.value = await api('/users') }
    catch { if (!accounts.value.some(account => account.id === created.id)) accounts.value = [...accounts.value, created] }
    flash('账号已创建')
  } catch (e: any) { error.value = e.message || String(e) }
  finally { busy.value = false }
}
function editAccountPermissions(account: any) {
  if (account.role === 'admin') return
  editingAccount.value = account
  editingPermissions.value = [...(account.permissions || [])]
  editingUsername.value = account.username
  resetPassword.value = ''
  resetPasswordConfirm.value = ''
  resetMustChange.value = true
}
async function saveAccountPermissions() {
  if (!editingAccount.value) return
  const username = editingUsername.value.trim()
  if (!/^[A-Za-z0-9_]{3,40}$/.test(username)) { error.value = '用户名须为 3-40 位字母、数字或下划线'; return }
  const saved = await act(async () => {
    if (username !== editingAccount.value.username)
      await api(`/users/${editingAccount.value.id}`, 'PUT', { username })
    return api(`/users/${editingAccount.value.id}/permissions`, 'PUT', { permissions: editingPermissions.value })
  }, '账号与权限已更新')
  if (saved) {
    accounts.value = accounts.value.map(account => account.id === saved.id ? saved : account)
    editingAccount.value = null
    editingPermissions.value = []
    if (saved.id === user.value.id) await refresh()
  }
}
async function resetAccountPassword() {
  if (!editingAccount.value) return
  if (resetPassword.value.length < 8) { error.value = '初始密码至少 8 位'; return }
  if (resetPassword.value !== resetPasswordConfirm.value) { error.value = '两次输入的初始密码不一致'; return }
  const saved = await act(() => api(`/users/${editingAccount.value.id}/reset-password`, 'POST', {
    password: resetPassword.value, must_change_password: resetMustChange.value,
  }), '密码已重置，原登录状态已失效')
  if (saved) {
    accounts.value = accounts.value.map(account => account.id === saved.id ? saved : account)
    editingAccount.value = saved
    resetPassword.value = ''
    resetPasswordConfirm.value = ''
  }
}
async function changeOwnPassword() {
  if (passwordChange.value.new_password.length < 8) { error.value = '新密码至少 8 位'; return }
  if (passwordChange.value.new_password !== passwordChange.value.confirm_password) { error.value = '两次输入的新密码不一致'; return }
  const data = await act(() => api('/auth/password', 'PUT', {
    current_password: passwordChange.value.current_password,
    new_password: passwordChange.value.new_password,
  }), '密码修改成功')
  if (data) {
    setToken(data.token)
    user.value = data.user
    passwordChange.value = { current_password: '', new_password: '', confirm_password: '' }
    await refresh()
  }
}
async function loadProviderModels(providerId: number | null) {
  if (!providerId) return
  providerLoading.value[providerId] = true
  try { providerModels.value[providerId] = (await api(`/providers/${providerId}/models`)).models; flash('模型列表已更新') }
  catch (e: any) { error.value = e.message || String(e) }
  finally { providerLoading.value[providerId] = false }
}
function resetProviderForm() {
  editingProviderId.value = null
  providerConnectionTest.value = null
  newProvider.value = { display_name: '', base_url: '', api_style: 'openai', api_key: '',
    ollama_scheme: 'http', ollama_host: 'host.docker.internal', ollama_port: 11434 }
}
function providerBaseUrl() {
  if (newProvider.value.api_style !== 'ollama') return newProvider.value.base_url.trim()
  const host = newProvider.value.ollama_host.trim()
  const formattedHost = host.includes(':') && !host.startsWith('[') ? `[${host}]` : host
  return `${newProvider.value.ollama_scheme}://${formattedHost}:${newProvider.value.ollama_port}/v1`
}
function providerPayload() {
  return { display_name: newProvider.value.display_name.trim(), base_url: providerBaseUrl(),
    api_style: newProvider.value.api_style, api_key: newProvider.value.api_key }
}
async function testProviderConnection() {
  if (newProvider.value.api_style === 'ollama' &&
      (!newProvider.value.ollama_host.trim() || newProvider.value.ollama_port < 1 || newProvider.value.ollama_port > 65535)) {
    error.value = '请填写有效的 Ollama 主机地址和端口'
    return
  }
  providerConnectionTest.value = null
  const payload = providerPayload()
  const result = await act(() => api('/providers/test-connection', 'POST', {
    base_url: payload.base_url, api_style: payload.api_style, api_key: payload.api_key,
  }), '提供商连接测试成功')
  if (result) providerConnectionTest.value = result
}
watch(newProvider, () => { providerConnectionTest.value = null }, { deep: true })
watch(tab, value => {
  if (value !== 'models') return
  for (const id of new Set([aiConfig.value.text_provider_id, aiConfig.value.vision_provider_id])) {
    if (id && !providerModels.value[id]) loadProviderModels(id)
  }
})
async function saveProvider() {
  const id = editingProviderId.value
  const saved = await act(() => api(id ? `/providers/${id}` : '/providers', id ? 'PUT' : 'POST', providerPayload()), '提供商已保存')
  if (saved) {
    providers.value = await api('/providers')
    resetProviderForm()
  }
}
function editProvider(provider: any) {
  editingProviderId.value = provider.id
  const next = { display_name: provider.display_name, base_url: provider.base_url,
    api_style: provider.api_style, api_key: '', ollama_scheme: 'http',
    ollama_host: 'host.docker.internal', ollama_port: 11434 }
  if (provider.api_style === 'ollama') {
    try {
      const parsed = new URL(provider.base_url)
      next.ollama_scheme = parsed.protocol.replace(':', '')
      next.ollama_host = parsed.hostname
      next.ollama_port = Number(parsed.port || (parsed.protocol === 'https:' ? 443 : 11434))
    } catch { /* Keep editable defaults for a legacy malformed address. */ }
  }
  newProvider.value = next
  providerConnectionTest.value = null
  tab.value = 'models'
}
async function saveAiConfig() {
  const saved = await act(() => api('/ai/config', 'PUT', aiConfig.value), '模型选择已保存')
  if (saved) { aiConfig.value = saved; aiStatus.value = await api('/ai/status') }
}
async function checkModel(kind: 'text' | 'vision') {
  const providerId = aiConfig.value[`${kind}_provider_id`]
  const model = aiConfig.value[`${kind}_model`]
  if (!providerId || !model) { error.value = '请先选择提供商和模型'; return }
  modelTest.value[kind] = null
  const result = await act(() => api(`/providers/${providerId}/test`, 'POST', { model, vision: kind === 'vision' }), '模型测试成功')
  if (result) modelTest.value[kind] = result
}
async function discoverToc() { if (bookId.value) { await act(() => api(`/books/${bookId.value}/discover-toc`, 'POST'), '目录解析任务已入队'); await refresh() } }
async function confirmToc() { if (bookId.value) { await act(() => api(`/books/${bookId.value}/confirm-toc`, 'POST'), '目录已确认'); await refresh() } }
async function startParse() { if (bookId.value) { await act(() => api(`/books/${bookId.value}/parse`, 'POST'), '章节正文解析任务已入队'); await refresh() } }
async function savePage() {
  if (!selectedPage.value) return
  const page = await act(() => api(`/pages/${selectedPage.value.id}`, 'PATCH', selectedPage.value), '页码与原文已保存')
  if (page) await loadBook(bookId.value!)
}
async function confirmMapping() { await act(() => api(`/books/${bookId.value}/confirm-mapping`, 'POST'), '目录映射已确认，索引已建立'); await refresh() }
async function saveToc() { const value = await act(() => api(`/books/${bookId.value}/toc`, 'PUT', tocDraft.value), '书签目录已保存'); if (value) await refresh() }
async function extractKnowledge() {
  if (!selectedChapter.value) { error.value = '请先选择章节'; return }
  await act(() => api(`/books/${bookId.value}/extract-knowledge`, 'POST', { chapter: selectedChapter.value }), '本章知识点提取任务已入队')
  await refresh()
}
function openChapterQuestions() {
  if (!chapterOutline.value.length) return
  selectedHeadingIndices.value = chapterOutline.value.map((heading: any) => heading.index)
  chapterQuestionCount.value = Math.min(10, Math.max(1, chapterOutline.value.length - 1))
  tab.value = 'questions'
}
async function generateChapterQuestions() {
  if (!selectedChapter.value) { error.value = '请先选择章节'; return }
  if (!headingSelectionReady.value) { error.value = '请选择目录标题；勾选大标题会自动包含小标题'; return }
  const selected = selectedHeadingIndices.value.length ? selectedHeadingIndices.value : [chapterOutline.value[0].index]
  await act(() => api(`/books/${bookId.value}/generate-chapter-questions`, 'POST', { chapter: selectedChapter.value, count: chapterQuestionCount.value, selected_heading_indices: selected }), '所选目录范围的出题任务已入队')
  await refresh()
}
function toggleHeading(index: number, checked: boolean) {
  const outline = chapterOutline.value
  const position = outline.findIndex((item: any) => item.index === index)
  if (position < 0) return
  const indices = [index]
  for (const item of outline.slice(position + 1)) {
    if (item.level <= outline[position].level) break
    indices.push(item.index)
  }
  const current = new Set(selectedHeadingIndices.value)
  for (const value of indices) checked ? current.add(value) : current.delete(value)
  if (!checked) {
    let level = outline[position].level
    for (let previous = position - 1; previous >= 0; previous--) {
      if (outline[previous].level < level) { current.delete(outline[previous].index); level = outline[previous].level }
    }
  }
  selectedHeadingIndices.value = [...current]
}
function chooseKnowledge(k?: any) {
  knowledgeEdit.value = k?.id || null
  knowledgeForm.value = k ? { title: k.title, content: k.content, chunk_id: k.chunk_id, source_quote: k.source_quote, chapter: k.chapter } : { title: '', content: '', chunk_id: null, source_quote: '', chapter: selectedChapter.value }
}
async function saveKnowledge() {
  const id = knowledgeEdit.value
  const path = id ? `/knowledge/${id}` : `/books/${bookId.value}/knowledge`
  const result = await act(() => api(path, id ? 'PATCH' : 'POST', knowledgeForm.value), '知识点已保存，等待审核')
  if (result) { chooseKnowledge(); await loadBook(bookId.value!) }
}
async function reviewKnowledge(id: number, approve: boolean) {
  await act(() => api(`/knowledge/${id}/review`, 'POST', { approve, note: approve ? '' : '请修正后重新提交' }), approve ? '知识点已通过' : '知识点已退回')
  await loadBook(bookId.value!)
}
async function deleteKnowledge(item: any) {
  if (!window.confirm(`删除知识点《${item.title}》？它及关联题目将退出当前题库，已发布试卷保留。`)) return
  const result = await act(() => api(`/knowledge/${item.id}`, 'DELETE'), '知识点已删除')
  if (result) {
    if (knowledgeEdit.value === item.id) chooseKnowledge()
    await loadBook(bookId.value!)
    if (questionForm.value.knowledge_id === item.id) chooseQuestion()
  }
}
async function deleteSelectedKnowledge() {
  const item = knowledge.value.find(k => k.id === knowledgeEdit.value)
  if (item) await deleteKnowledge(item)
}
async function generateQuestion(id: number) { await act(() => api(`/knowledge/${id}/generate-question`, 'POST'), '出题任务已入队'); await refresh() }
function chooseQuestion(q?: any) {
  questionEdit.value = q?.id || null
  questionForm.value = q ? { knowledge_id: q.knowledge_id, stem: q.stem, options: { ...q.options }, answer: q.answer, explanation: q.explanation, evidence: q.evidence, difficulty: q.difficulty, variant_of: q.variant_of } : { knowledge_id: approvedChapterKnowledge.value[0]?.id || 0, stem: '', options: { A: '', B: '', C: '', D: '' }, answer: 'A', explanation: '', evidence: '', difficulty: 'medium', variant_of: null }
}
function onChapterChange() {
  selectedPage.value = null
  selectedHeadingIndices.value = []
  paperForm.value.question_ids = []
  chooseKnowledge()
  chooseQuestion()
}
async function saveQuestion() {
  const id = questionEdit.value
  const q = await act(() => api(id ? `/questions/${id}` : '/questions', id ? 'PATCH' : 'POST', questionForm.value), '题目已保存并自动校验')
  if (q) { chooseQuestion(); await loadBook(bookId.value!) }
}
async function reviewQuestion(id: number, approve: boolean) {
  await act(() => api(`/questions/${id}/review`, 'POST', { approve, note: approve ? '' : '请修改后重新校验' }), approve ? '题目已通过' : '题目已退回')
  await loadBook(bookId.value!)
}
async function makePaper() {
  const paper = await act(() => api('/papers', 'POST', { ...paperForm.value, book_id: bookId.value,
    chapter: selectedChapter.value, question_count: paperForm.value.question_ids.length }), '试卷草稿已生成')
  if (paper) await refresh()
}
async function publishPaper(id: number) {
  if (!publishStart.value || !publishEnd.value) { error.value = '请填写考试开始和结束时间'; return }
  const value = await act(() => api(`/papers/${id}/publish`, 'POST', { starts_at: new Date(publishStart.value).toISOString(), ends_at: new Date(publishEnd.value).toISOString() }), '试卷已发布并冻结题目与评分规则')
  if (value) await refresh()
}
function editPaperTime(paper: any) {
  paperTimeEdit.value = paper.id
  const display_start = localDateTime(paper.starts_at)
  const display_end = localDateTime(paper.ends_at)
  paperTimeForm.value = { starts_at: display_start, ends_at: display_end }
  paperTimeOriginal.value = { starts_at: paper.starts_at, ends_at: paper.ends_at, display_start, display_end }
}
async function savePaperTime(id: number) {
  if (!paperTimeForm.value.starts_at || !paperTimeForm.value.ends_at) { error.value = '请填写考试开始和结束时间'; return }
  const value = await act(() => api(`/papers/${id}/schedule`, 'PATCH', {
    starts_at: paperTimeForm.value.starts_at === paperTimeOriginal.value.display_start ? paperTimeOriginal.value.starts_at : new Date(paperTimeForm.value.starts_at).toISOString(),
    ends_at: paperTimeForm.value.ends_at === paperTimeOriginal.value.display_end ? paperTimeOriginal.value.ends_at : new Date(paperTimeForm.value.ends_at).toISOString(),
  }), '考试时间已更新')
  if (value) { paperTimeEdit.value = null; await refresh() }
}
async function terminatePaper(paper: any) {
  if (!window.confirm(`提前终止《${paper.title}》？未交卷答卷将立即按已保存答案判分。`)) return
  const value = await act(() => api(`/papers/${paper.id}/terminate`, 'POST'), '考试已终止，未交卷答卷已判分')
  if (value) await refresh()
}
async function deletePaper(paper: any) {
  if (!window.confirm(`删除${paper.status === 'draft' ? '草稿' : '试卷'}《${paper.title}》？${paper.status === 'draft' ? '该草稿将从列表移除。' : '历史答卷和已发布快照仍会保留。'}`)) return
  const value = await act(() => api(`/papers/${paper.id}`, 'DELETE'), '试卷已从列表移除')
  if (value) { paperTimeEdit.value = null; await refresh() }
}
async function startExam(id: number) {
  const a = await act(() => api(`/papers/${id}/start`, 'POST'), '答卷已打开')
  if (a) { if (a.submitted) await openResult(a.id); else { currentAttempt.value = a; result.value = null; tab.value = 'exam' } }
}
async function saveAnswer(qid: number, answer: string) {
  if (!currentAttempt.value) return
  const attemptId = currentAttempt.value.id
  const saved = await act(() => api(`/attempts/${attemptId}/answer`, 'PUT', { question_id: qid, answer }), '答案已自动保存')
  if (!saved) {
    if (error.value.includes('考试已结束')) await openResult(attemptId)
    return
  }
  if (currentAttempt.value?.id === attemptId) currentAttempt.value.answers = { ...currentAttempt.value.answers, [qid]: answer }
}
async function submitExam() {
  if (!currentAttempt.value) return
  const data = await act(() => api(`/attempts/${currentAttempt.value.id}/submit`, 'POST'), '交卷成功')
  if (data) { await openResult(currentAttempt.value.id); await refresh() }
}
async function openResult(id: number) {
  const data = await act(() => api(`/attempts/${id}`))
  if (data) { result.value = data; currentAttempt.value = null; tab.value = 'results' }
}
async function syncCurrentAttempt() {
  const active = currentAttempt.value
  if (!active) return
  try {
    const updated = await api(`/attempts/${active.id}`)
    if (currentAttempt.value?.id !== active.id) return
    if (updated.submitted_at) {
      result.value = updated; currentAttempt.value = null; tab.value = 'results'
      flash('考试已结束，答卷已按已保存答案判分')
      await refresh()
    } else if (updated.ends_at) {
      currentAttempt.value = { ...currentAttempt.value, ends_at: updated.ends_at, answers: updated.answers }
    }
  } catch { /* A transient poll error does not affect saved answers. */ }
}
async function startPractice(row: any, mode: string) {
  const data = await act(() => api('/practices', 'POST', { source_attempt_id: result.value.id, question_id: row.question_id, mode }), mode === 'variant' ? '变式请求已提交' : '原题练习已开始')
  if (data?.pending_review) { practice.value = null; await refresh(); if (data.job?.status === 'failed') error.value = data.job.error || '变式生成失败' }
  else if (data) practice.value = { ...data, selected: '' }
}
async function answerPractice() {
  if (!practice.value?.selected) return
  const data = await act(() => api(`/practices/${practice.value.practice.id}/answer`, 'POST', { answer: practice.value.selected }), '练习已判分')
  if (data) practice.value.feedback = data
}
async function cancelJob(id: number) { await act(() => api(`/jobs/${id}/cancel`, 'POST'), '已请求取消'); await refresh() }
async function retryJob(id: number) { await act(() => api(`/jobs/${id}/retry`, 'POST'), '任务已重新入队'); await refresh() }
async function clearFinishedJobs() {
  if (!window.confirm('清空已完成、失败和已取消的任务列表？运行中的任务会保留。')) return
  const result = await act(() => api('/jobs', 'DELETE'), '已清空已结束任务')
  if (result) jobs.value = await api('/jobs')
}
function tick() {
  const end = currentAttempt.value?.ends_at
  if (!end) { remaining.value = ''; return }
  const seconds = Math.max(0, Math.floor((new Date(`${end}Z`).getTime() - Date.now()) / 1000))
  remaining.value = `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
  if (!seconds && currentAttempt.value) openResult(currentAttempt.value.id)
}
let interval: ReturnType<typeof setInterval> | undefined
let examPolls = 0
onMounted(async () => {
  if (token) { try { user.value = await api('/auth/me'); if (!user.value.must_change_password) await refresh() } catch { setToken('') } }
  interval = setInterval(() => {
    tick()
    if (currentAttempt.value && ++examPolls % 3 === 0) syncCurrentAttempt()
    if (user.value && canViewJobs.value) api('/jobs').then(data => {
      const finished = data.some((job: any) => ['done', 'failed', 'cancelled'].includes(job.status) &&
        jobs.value.some(old => old.id === job.id && ['queued', 'running', 'retrying'].includes(old.status)))
      jobs.value = data
      if (finished && !busy.value) refresh().catch(() => {})
    }).catch(() => {})
  }, 5000)
})
onUnmounted(() => { if (interval) clearInterval(interval); clearSourceImages() })
</script>

<template>
  <div class="shell">
    <header class="topbar"><div class="brand"><span class="brand-mark">A</span><div><strong>Abook</strong><small>教材 · 知识 · 考试</small></div></div><div v-if="user" class="account"><span class="role-tag">{{ user.role === 'admin' ? '管理员' : user.role === 'teacher' ? '教师' : '学生' }}</span><span>{{ user.username }}</span><button class="text-btn" @click="logout">退出</button></div></header>
    <main v-if="!user" class="login-wrap"><section class="login-hero"><p class="eyebrow">LEARNING OPERATIONS</p><h1>从一页教材<br>到一次有效学习。</h1><p>解析、审核、出题、考试与错题练习，都沿着原文出处有序推进。</p><div class="flow-mini"><span>教材 PDF</span><i>→</i><span>知识点</span><i>→</i><span>题库</span><i>→</i><span>考试</span></div></section><section class="login-card"><p class="eyebrow">WELCOME BACK</p><h2>登录工作台</h2><label>用户名<input v-model="auth.username" autocomplete="username" /></label><label>密码<input v-model="auth.password" type="password" autocomplete="current-password" @keyup.enter="login" /></label><button class="primary full" :disabled="busy" @click="login">登录</button><p v-if="error" class="alert error">{{ error }}</p></section></main>
    <main v-else-if="user.must_change_password" class="password-change-wrap"><section class="login-card password-change-card"><p class="eyebrow">PASSWORD REQUIRED</p><h2>请先修改初始密码</h2><p class="muted">当前密码由管理员重置。修改为自己的密码后才能进入系统。</p><label>当前初始密码<input v-model="passwordChange.current_password" type="password" autocomplete="current-password" /></label><label>新密码<input v-model="passwordChange.new_password" type="password" autocomplete="new-password" placeholder="至少 8 位" /></label><label>确认新密码<input v-model="passwordChange.confirm_password" type="password" autocomplete="new-password" @keyup.enter="changeOwnPassword" /></label><button class="primary full" :disabled="busy" @click="changeOwnPassword">修改密码并进入系统</button><button class="text-btn full" @click="logout">退出登录</button><p v-if="error" class="alert error">{{ error }}</p></section></main>
    <div v-else class="workspace"><aside class="sidebar"><div class="side-label">工作区</div><button :class="{ active: tab === 'overview' }" @click="tab = 'overview'">◫ <span>总览</span></button><button v-if="canViewBooks" :class="{ active: tab === 'books' }" @click="tab = 'books'">▤ <span>教材与学习</span></button><button v-if="canViewKnowledge" :class="{ active: tab === 'knowledge' }" @click="tab = 'knowledge'">◇ <span>知识点</span></button><button v-if="canViewQuestions" :class="{ active: tab === 'questions' }" @click="tab = 'questions'">☷ <span>试题</span></button><button v-if="canManagePapers" :class="{ active: tab === 'papers' }" @click="tab = 'papers'">▣ <span>组卷与发布</span></button><button v-if="canViewJobs" :class="{ active: tab === 'jobs' }" @click="tab = 'jobs'">◴ <span>后台任务</span></button><button v-if="canViewModels" :class="{ active: tab === 'models' }" @click="tab = 'models'">✦ <span>模型接入</span></button><button v-if="canViewUsers" :class="{ active: tab === 'users' }" @click="tab = 'users'">♙ <span>账号与权限</span></button><button v-if="canExam" :class="{ active: tab === 'exam' }" @click="tab = 'exam'">✎ <span>参加考试</span></button><button v-if="canResults" :class="{ active: tab === 'results' }" @click="tab = 'results'">◈ <span>成绩与错题</span></button><div class="side-footer">PDF · 单选题 · 人工审核<br>第一版闭环</div></aside>
      <div class="content" :class="{ 'question-content': tab === 'questions' }"><div v-if="error" class="alert error"><span>{{ error }}</span><button @click="error = ''">×</button></div><div v-if="notice" class="alert success">{{ notice }}</div>
        <section v-if="tab === 'overview'"><div class="page-head"><div><p class="eyebrow">WORKSPACE</p><h1>工作总览</h1><p>按教材原文、审核记录和试卷快照，追踪每一步。</p></div><button class="secondary" @click="refresh">刷新数据</button></div><div class="stat-grid"><div class="stat"><span>教材库</span><strong>{{ books.length }}</strong><small>原 PDF 留存</small></div><div class="stat"><span>{{ isStaff ? '待审核知识点' : '已参加考试' }}</span><strong>{{ isStaff ? knowledge.filter(k => k.status !== 'approved').length : attempts.length }}</strong><small>{{ isStaff ? '需要人工判断' : '每场考试限一次交卷' }}</small></div><div class="stat"><span>{{ isStaff ? '待审核试题' : '知识点掌握' }}</span><strong>{{ isStaff ? questions.filter(q => q.status !== 'approved').length : Object.keys(mastery).length }}</strong><small>{{ isStaff ? '校验后方可发布' : '依据答卷记录' }}</small></div><div class="stat"><span>已发布试卷</span><strong>{{ papers.filter(p => p.status === 'published').length }}</strong><small>答案与规则固定</small></div></div><div class="panel"><h2>闭环进度</h2><div class="steps"><div v-for="(step, i) in ['上传 PDF', '校对索引', '知识点审核', '试题审核', '组卷发布', '判分重练']" :key="step"><b>{{ String(i + 1).padStart(2, '0') }}</b><span>{{ step }}</span></div></div></div><div v-if="canViewModels" class="panel"><div class="panel-head"><h2>模型接入</h2><button class="small" @click="tab = 'models'">管理提供商</button></div><div class="source-line"><span>文本：{{ aiStatus?.text?.provider || '未设置' }} / {{ aiStatus?.text?.model || '未设置' }}</span><span>视觉 OCR：{{ aiStatus?.vision?.provider || '未设置' }} / {{ aiStatus?.vision?.model || '未设置' }}</span></div></div><div v-if="canViewJobs && jobs.some(j => j.status === 'failed')" class="panel warning"><h3>需要处理的任务</h3><p>有任务失败。打开“后台任务”查看错误，并重试或人工修正。</p></div></section>

        <section v-if="tab === 'books' && canViewBooks">
          <div class="page-head"><div><p class="eyebrow">SOURCE LIBRARY</p><h1>教材目录与章节</h1><p>先确定目录和 PDF 起始页，再用视觉模型核对章节边界，最后建立可追溯的学习内容。</p></div></div>
          <div v-if="canManageBooks" class="panel form-panel">
            <h2>上传教材到教材库</h2><div class="form-row"><label>教材名称<input v-model="uploadTitle" placeholder="如：OpenStack" /></label><label>PDF 文件<input type="file" accept="application/pdf,.pdf" @change="uploadFile = ($event.target as HTMLInputElement).files?.[0] || null" /></label></div>
            <div class="form-row"><label>目录所在 PDF 页（可选）<input v-model="uploadTocPages" placeholder="如：6-13；留空则自动查找" /></label><label>手工目录（可选，每行：层级|章节标题|PDF起始页）<textarea v-model="uploadManualToc" rows="3" placeholder="1|项目1 初识云计算|15&#10;1|项目2 安装系统|25"></textarea></label></div>
            <p class="muted">有手工目录时优先使用；没有时先读取 PDF 书签或目录页。起始页是 PDF 页码，导入后仍可逐条校对。</p><button class="primary" :disabled="busy || !uploadTitle || !uploadFile" @click="upload">保存教材与目录设置</button>
          </div>
          <div class="panel"><div class="panel-head"><h2>教材库</h2><span>{{ books.length }} 本教材</span></div><div v-if="books.length" class="library-grid"><div v-for="b in books" :key="b.id" class="library-book" :class="{ selected: b.id === bookId }"><button class="library-book-select" @click="loadBook(b.id)"><strong>{{ b.title }}</strong><span>{{ b.page_count }} 页 · {{ statusName(b.status) }}</span><small>导入于 {{ time(b.created_at) }}</small></button><button v-if="canManageBooks" class="small danger-btn" :disabled="busy" @click="deleteBook(b)">删除教材</button></div></div><p v-else class="empty">教材库尚无教材，请上传 PDF。</p></div>
          <div v-if="selectedBook" class="selector-row"><span>当前教材</span><strong>{{ selectedBook.title }}</strong><span class="badge">{{ statusName(selectedBook.status) }}</span><button class="text-btn" @click="refresh">刷新教材</button></div>
          <template v-if="selectedBook">
            <div v-if="canManageBooks" class="panel"><h2>处理顺序</h2><div class="action-strip"><button class="secondary" :disabled="busy || catalog?.confirmed" @click="discoverToc">① 解析目录</button><button class="secondary" :disabled="busy || !tocDraft.length || catalog?.confirmed || tocDirty" @click="confirmToc">② 确认目录</button><button class="secondary" :disabled="busy || !catalog?.confirmed" @click="startParse">③ 视觉核对并解析章节</button><button class="primary" :disabled="busy || !catalog?.classified || pages.length !== selectedBook.page_count" @click="confirmMapping">④ 确认章节 / 建立索引</button></div><p class="muted">{{ selectedBook.page_count }} 页 · {{ catalog?.chapters?.length || 0 }} 章/项目。目录发现后请先保存修正，再确认。解析任务进度在“后台任务”查看。</p></div>
            <div v-if="canManageBooks" class="panel"><div class="panel-head"><h2>章节目录</h2><div class="button-row"><button class="small" @click="tocDraft.push({ level: 1, title: '', pdf_page: selectedBook.page_count || 1 })">添加章节</button><button class="small" @click="tocDraft.push({ level: 2, title: '', pdf_page: selectedBook.page_count || 1 })">添加小标题</button></div></div>
              <p class="muted">来源：{{ catalog?.source === 'manual' ? '人工录入' : catalog?.source === 'bookmarks' ? 'PDF 书签' : catalog?.source === 'directory' ? '目录页' : '待解析' }} · {{ catalog?.confirmed ? '已确认' : '待人工确认' }}</p>
              <div v-for="warning in catalog?.warnings || []" :key="warning" class="issue">{{ warning }}</div>
              <div v-if="tocDraft.length" class="toc-edit"><div v-for="(t, i) in tocDraft" :key="i"><input v-model.number="t.level" type="number" min="1" max="8" aria-label="层级" /><input v-model="t.title" aria-label="章节标题" /><input v-model.number="t.pdf_page" type="number" min="1" aria-label="PDF 起始页" /><button class="text-btn" @click="tocDraft.splice(i, 1)">移除</button></div></div><p v-else class="empty">尚无目录。点击“解析目录”，或添加章节并填写 PDF 起始页。</p>
              <button v-if="canManageBooks" class="primary" :disabled="busy || !tocDraft.length" @click="saveToc">保存目录修正</button><details v-if="catalog?.raw_text" class="paper-preview"><summary>查看提取的目录原文</summary><pre class="catalog-raw">{{ catalog.raw_text }}</pre></details>
            </div>
            <div v-if="chapterNames.length" class="selector-row"><span>当前章节</span><select v-model="selectedChapter" @change="onChapterChange"><option v-if="pages.some(p => p.chapter === '前置内容')" value="前置内容">前置内容（封面/目录）</option><option v-for="chapter in chapterNames" :key="chapter" :value="chapter">{{ chapter }}</option></select><span>{{ chapterPages.length }} 页 · {{ chapterKnowledge.length }} 条知识点</span></div>
            <div v-if="canManageBooks && pages.length" class="two-col"><div class="panel"><div class="panel-head"><h2>本章页面校对</h2><span>{{ chapterPages.filter(p => p.issues?.length).length }} 页需核查</span></div><div class="page-list"><button v-for="p in chapterPages" :key="p.id" :class="{ selected: selectedPage?.id === p.id }" @click="selectedPage = { ...p }"><span>PDF {{ p.pdf_page }} / 印刷 {{ p.printed_page }}</span><small>{{ p.chapter }}</small><em v-if="p.issues?.length">{{ p.issues.length }} 项</em></button></div><div v-if="!chapterPages.length" class="empty">本章尚无页面，检查目录起始页或等待解析。</div></div>
              <div class="panel" v-if="selectedPage"><h2>PDF 第 {{ selectedPage.pdf_page }} 页</h2><div v-for="issue in selectedPage.issues" :key="issue" class="issue">{{ issue }}</div><label>印刷页码<input v-model="selectedPage.printed_page" /></label><label>所属章节<select v-model="selectedPage.chapter"><option value="前置内容">前置内容</option><option v-for="chapter in chapterNames" :key="chapter" :value="chapter">{{ chapter }}</option></select></label><label>原文<textarea v-model="selectedPage.text" rows="12"></textarea></label><label>未处理问题（每行一项）<textarea :value="selectedPage.issues.join('\n')" rows="3" @input="selectedPage.issues = ($event.target as HTMLTextAreaElement).value.split('\n').filter(Boolean)"></textarea></label><button v-if="canManageBooks" class="primary" @click="savePage">保存修正</button></div><div v-else class="panel empty">选择本章的一页，查看视觉分类和原文。</div></div>
            <div class="panel"><div class="panel-head"><div><h2>本章结构化学习</h2><p class="muted">知识点和试题按章节组织；原文块仍保留 PDF 页码供核对出处。</p></div></div><div v-if="chapterSections.length" class="learning-list"><article v-for="section in chapterSections" :key="section.chunk_id" class="learning-item"><div class="source-line"><strong>{{ section.chapter }}</strong><span>PDF {{ section.pdf_page }} · 印刷 {{ section.printed_page }}</span><span>原文块 #{{ section.chunk_id }}</span></div><p>{{ section.text }}</p><div v-if="section.knowledge.length" class="knowledge-tags"><span v-for="k in section.knowledge" :key="k.id">{{ k.title }}</span></div></article></div><div v-else class="empty">确认章节解析与索引后，在这里阅读本章内容。</div></div>
          </template><div v-else class="panel empty">尚无教材。请由管理员上传 PDF。</div>
        </section>

        <section v-if="tab === 'knowledge' && canViewKnowledge">
          <div class="page-head"><div><p class="eyebrow">CHAPTER KNOWLEDGE</p><h1>按章节总结与审核</h1><p>只在需要时提取所选章节；每条知识点仍绑定可核对的原文块。</p></div><div class="button-row"><button v-if="canEditQuestions" class="secondary" :disabled="!approvedChapterKnowledge.length" @click="openChapterQuestions">按本章目录出题</button><button v-if="canEditKnowledge" class="primary" :disabled="busy || !selectedBook?.mapping_confirmed || !chapterNames.includes(selectedChapter)" @click="extractKnowledge">总结本章知识点</button></div></div>
          <div class="selector-row"><span>章节</span><select v-model="selectedChapter" @change="onChapterChange"><option v-for="chapter in chapterNames" :key="chapter" :value="chapter">{{ chapter }}</option></select><span>{{ chapterKnowledge.length }} 条知识点</span></div>
          <div class="two-col" :class="{ 'single-col': !canEditKnowledge }"><div class="panel"><div class="panel-head"><h2>本章知识点</h2><span>{{ chapterKnowledge.length }} 条</span></div>
            <div v-for="k in chapterKnowledge" :key="k.id" class="record"><div class="record-top"><strong>{{ k.title }}</strong><span class="badge" :class="k.status">{{ statusName(k.status) }}</span></div><p>{{ k.content }}</p><div class="source-line"><span>{{ k.chapter }}</span><span>{{ k.origin === 'manual' ? '人工补充' : 'AI 总结' }}</span><span>原文块 #{{ k.chunk_id || '无' }}</span></div><blockquote v-if="k.source_quote">{{ k.source_quote }}</blockquote><p v-if="k.review_note" class="muted">审核意见：{{ k.review_note }}</p><div class="button-row"><button v-if="canEditKnowledge" class="text-btn" @click="chooseKnowledge(k)">编辑</button><button v-if="canReviewKnowledge && k.status !== 'approved'" class="small success-btn" @click="reviewKnowledge(k.id, true)">通过</button><button v-if="canReviewKnowledge && k.status !== 'rejected'" class="small danger-btn" @click="reviewKnowledge(k.id, false)">退回</button><button v-if="canEditQuestions && k.status === 'approved' && k.chunk_id" class="small" @click="generateQuestion(k.id)">仅用此知识点出题</button><button v-if="canEditKnowledge" class="small danger-btn" :disabled="busy" @click="deleteKnowledge(k)">删除知识点</button></div></div><div v-if="!chapterKnowledge.length" class="empty">本章尚未总结知识点。确认索引后点击“总结本章知识点”。</div></div>
            <div v-if="canEditKnowledge" class="panel form-panel"><h2>{{ knowledgeEdit ? `编辑知识点 #${knowledgeEdit}` : '人工补充本章知识点' }}</h2><label>标题<input v-model="knowledgeForm.title" /></label><label>内容<textarea v-model="knowledgeForm.content" rows="5"></textarea></label><label>出处内容块<select v-model.number="knowledgeForm.chunk_id"><option :value="null">无出处（只能内部整理）</option><option v-for="section in chapterSections" :key="section.chunk_id" :value="section.chunk_id">#{{ section.chunk_id }} · PDF {{ section.pdf_page }}</option></select></label><label>原文逐字摘录<textarea v-model="knowledgeForm.source_quote" rows="3"></textarea></label><p class="muted">所属章节：{{ selectedChapter || '请先选择章节' }}</p><div class="button-row"><button class="primary" :disabled="busy || !selectedChapter" @click="knowledgeForm.chapter = selectedChapter; saveKnowledge()">保存并提交审核</button><button class="secondary" @click="chooseKnowledge()">清空</button><button v-if="knowledgeEdit && canEditKnowledge" class="danger-btn" :disabled="busy" @click="deleteSelectedKnowledge">删除当前知识点</button></div></div>
          </div>
        </section>

        <section v-if="tab === 'questions' && canViewQuestions" class="question-workspace">
          <div class="page-head"><div><p class="eyebrow">CHAPTER QUESTIONS</p><h1>按教材目录出题与审核</h1><p>先选择教材和章节，再勾选出题范围；题目依据来自该范围内已审核知识点。</p></div><div v-if="canEditQuestions" class="button-row"><input v-model.number="chapterQuestionCount" type="number" min="1" max="30" aria-label="生成题数" class="count-input" /><button class="primary" :disabled="busy || !selectedBook?.mapping_confirmed || !approvedChapterKnowledge.length || !headingSelectionReady" @click="generateChapterQuestions">按所选标题出题</button></div></div>
          <div class="selector-row"><span>① 教材</span><select :value="bookId || ''" @change="loadBook(Number(($event.target as HTMLSelectElement).value))"><option v-for="b in books" :key="b.id" :value="b.id">{{ b.title }} · {{ b.page_count }} 页</option></select><span>② 章节</span><select v-model="selectedChapter" @change="onChapterChange"><option v-for="chapter in chapterNames" :key="chapter" :value="chapter">{{ chapter }}</option></select><span>{{ chapterQuestions.length }} 道题 · {{ approvedChapterKnowledge.length }} 条已审核知识点</span></div>
          <div v-if="chapterOutline.length" class="panel"><div class="panel-head"><h2>③ 选择目录标题</h2><span>勾选大标题将自动包含下方所有小标题</span></div><div class="heading-tree"><label v-for="heading in chapterOutline" :key="heading.index" :style="{ paddingLeft: `${12 + (heading.level - chapterOutline[0].level) * 22}px` }"><input type="checkbox" :disabled="chapterOutline.length === 1" :checked="selectedHeadingIndices.includes(heading.index) || (chapterOutline.length === 1 && heading.index === chapterOutline[0].index)" @change="toggleHeading(heading.index, ($event.target as HTMLInputElement).checked)" /><strong>{{ heading.title }}</strong><small>PDF {{ heading.pdf_page }}</small></label></div><p v-if="chapterOutline.length > 1 && !selectedHeadingIndices.length" class="muted">请选择至少一个标题。选择章标题会包含整章。</p></div>
          <div class="two-col question-columns" :class="{ 'single-col': !canEditQuestions }"><div class="panel"><div class="panel-head"><h2>本章试题</h2><span>{{ chapterQuestions.length }} 道</span></div>
            <div v-for="q in chapterQuestions" :key="q.id" class="record">
              <div class="record-top"><strong>#{{ q.id }} · {{ q.stem }}</strong><span class="badge" :class="q.status">{{ statusName(q.status) }}</span></div>
              <div v-if="q.image_pdf_page && bookId" class="figure-preview"><span>教材插图局部裁剪 · PDF 第 {{ q.image_pdf_page }} 页</span><a v-if="sourceImageUrl(bookId, q.image_pdf_page, q.chunk_id)" :href="sourceImageUrl(bookId, q.image_pdf_page, q.chunk_id)" target="_blank" rel="noopener"><img :src="sourceImageUrl(bookId, q.image_pdf_page, q.chunk_id)" :alt="`试题 #${q.id} 的教材插图局部裁剪`" /></a><p v-else class="muted">{{ sourceImageError(bookId, q.image_pdf_page, q.chunk_id) || '插图加载中…' }}</p></div>
              <div class="option-preview"><span v-for="key in ['A','B','C','D']" :key="key" :class="{ correct: q.answer === key }">{{ key }}. {{ q.options[key] }}</span></div>
              <p class="muted">解析：{{ q.explanation }}</p><blockquote>{{ q.evidence }}</blockquote>
              <div v-for="problem in q.validation" :key="problem" :class="q.status === 'approved' ? 'reviewed-issue' : 'issue'">{{ q.status === 'approved' ? `人工已确认：${problem}` : problem }}</div>
              <div class="source-line"><span>知识点 #{{ q.knowledge_id }}</span><span>原文块 #{{ q.chunk_id }}</span><span>版本 {{ q.revision }}</span><span v-if="q.variant_of">变式自 #{{ q.variant_of }}</span></div>
              <div class="button-row"><button v-if="canEditQuestions" class="text-btn" @click="chooseQuestion(q)">编辑</button><button v-if="canReviewQuestions && q.status !== 'approved'" class="small success-btn" @click="reviewQuestion(q.id, true)">审核通过</button><button v-if="canReviewQuestions && q.status !== 'rejected'" class="small danger-btn" @click="reviewQuestion(q.id, false)">退回</button></div>
            </div><div v-if="!chapterQuestions.length" class="empty">本章尚无题目。先审核本章知识点，再生成题目。</div></div>
            <div v-if="canEditQuestions" class="panel form-panel">
              <h2>{{ questionEdit ? `编辑试题 #${questionEdit}` : '录入本章单选题' }}</h2>
              <label>本章知识点<select v-model.number="questionForm.knowledge_id"><option v-for="k in approvedChapterKnowledge" :key="k.id" :value="k.id">#{{ k.id }} {{ k.title }}</option></select></label>
              <div v-if="questionSource" class="source-preview"><span>该知识点的教材原文摘录</span><blockquote>{{ questionSource }}</blockquote><button class="small" @click="questionForm.evidence = questionSource">填入原文依据</button><p class="muted">填入后仍需核对题干、答案与解析是否由这段原文支持。</p></div>
              <div v-if="questionSourcePage && bookId" class="figure-preview"><span>出处 PDF 第 {{ questionSourcePage }} 页原图 · 点击放大</span><a v-if="sourceImageUrl(bookId, questionSourcePage)" :href="sourceImageUrl(bookId, questionSourcePage)" target="_blank" rel="noopener"><img :src="sourceImageUrl(bookId, questionSourcePage)" alt="当前知识点的教材原文页图" /></a><p v-else class="muted">{{ sourceImageError(bookId, questionSourcePage) || '原文页图加载中…' }}</p><p class="muted">题干引用图号时，只会向考生提供自动定位的插图局部；无法可靠定位时不配图。审核时仍需检查裁剪区域是否包含直接答案。</p></div>
              <label>题干<textarea v-model="questionForm.stem" rows="3"></textarea></label>
              <label v-for="key in ['A','B','C','D']" :key="key">选项 {{ key }}<input v-model="questionForm.options[key]" /></label>
              <div class="form-row"><label>正确答案<select v-model="questionForm.answer"><option v-for="key in ['A','B','C','D']" :key="key">{{ key }}</option></select></label><label>难度<select v-model="questionForm.difficulty"><option value="easy">简单</option><option value="medium">中等</option><option value="hard">困难</option></select></label></div>
              <label>解析<textarea v-model="questionForm.explanation" rows="3"></textarea></label><label>原文依据<textarea v-model="questionForm.evidence" rows="3"></textarea></label>
              <div class="button-row"><button class="primary" :disabled="busy || !approvedChapterKnowledge.length" @click="saveQuestion">保存并自动校验</button><button class="secondary" @click="chooseQuestion()">清空</button></div>
            </div>
          </div>
        </section>

        <section v-if="tab === 'papers' && canManagePapers">
          <div class="page-head"><div><p class="eyebrow">EXAM RELEASE</p><h1>组卷与发布</h1><p>发布时冻结题目、答案、解析和分值，历史答卷按快照判分。</p></div></div>
          <div v-if="canManagePapers" class="panel form-panel"><h2>新建本章试卷草稿</h2><label>章节<select v-model="selectedChapter" @change="onChapterChange"><option v-for="chapter in chapterNames" :key="chapter" :value="chapter">{{ chapter }}</option></select></label><div class="form-row"><label>试卷名称<input v-model="paperForm.title" placeholder="如：第一章测验" /></label><label>每题分值<input v-model.number="paperForm.score_each" type="number" min="1" /></label><label>难度筛选<select v-model="paperForm.difficulty" @change="paperForm.question_ids = []"><option value="any">不限</option><option value="easy">简单</option><option value="medium">中等</option><option value="hard">困难</option></select></label></div><div class="panel-head"><h3>选择已审核考题</h3><span>{{ paperForm.question_ids.length }} / {{ paperCandidateQuestions.length }} 道已选</span></div><div class="paper-question-list"><label v-for="q in paperCandidateQuestions" :key="q.id" class="paper-question-option"><input v-model="paperForm.question_ids" type="checkbox" :value="q.id" /><span><strong>#{{ q.id }} · {{ q.stem }}</strong><small>{{ Object.entries(q.options).map(([key, value]) => `${key}. ${value}`).join(' · ') }}</small><small>{{ { easy: '简单', medium: '中等', hard: '困难' }[q.difficulty as 'easy' | 'medium' | 'hard'] || q.difficulty }} · {{ knowledge.find(k => k.id === q.knowledge_id)?.title }}</small></span></label><p v-if="!paperCandidateQuestions.length" class="empty">本章没有符合难度的已审核考题，请先到“试题审核”完成审核。</p></div><p class="muted">已选 {{ paperForm.question_ids.length }} 题，预估总分 {{ paperForm.question_ids.length * paperForm.score_each }} 分。试卷将使用勾选的考题。</p><button class="primary" :disabled="busy || !paperForm.title.trim() || !paperForm.question_ids.length || paperForm.question_ids.length > 100" @click="makePaper">生成试卷草稿</button></div>
          <div class="panel"><div class="panel-head"><h2>试卷列表</h2><div class="button-row"><span>{{ visiblePapers.length }} 份</span><button class="small" :disabled="busy" @click="refresh">刷新列表</button></div></div>
            <div v-for="paper in visiblePapers" :key="paper.id" class="record">
              <div class="record-top"><strong>{{ paper.title }}</strong><div class="button-row"><span class="badge" :class="paper.status === 'published' && (paper.can_delete || paperEnded(paper)) ? 'done' : paper.status">{{ paperLabel(paper) }}</span><button v-if="paperCanDelete(paper)" class="small danger-btn" :disabled="busy" @click="deletePaper(paper)">删除试卷</button></div></div>
              <div class="source-line"><span>教材：{{ books.find(b => b.id === paper.book_id)?.title || '已从教材库移除' }}</span><span>{{ paper.total_score }} 分</span><span>{{ paper.rule?.question_ids?.length || paper.snapshot?.length }} 题</span><span v-if="paper.rule">覆盖率 {{ Math.round(paper.rule.coverage * 100) }}%</span></div>
              <p v-if="paper.starts_at && paper.ends_at" class="muted">{{ time(paper.starts_at) }} 至 {{ time(paper.ends_at) }}</p>
              <details class="paper-preview"><summary>预览试卷与评分规则</summary><div v-for="(q, i) in paperPreview(paper)" :key="q.id"><strong>{{ i + 1 }}. {{ q.stem }}（{{ q.score }} 分）</strong><div v-if="q.image_pdf_page" class="figure-preview"><button v-if="!sourceImageUrl(paper.book_id, q.image_pdf_page, q.chunk_id)" class="small" @click="loadSourceImage(paper.book_id, q.image_pdf_page, q.chunk_id)">查看教材插图</button><a v-else :href="sourceImageUrl(paper.book_id, q.image_pdf_page, q.chunk_id)" target="_blank" rel="noopener"><img :src="sourceImageUrl(paper.book_id, q.image_pdf_page, q.chunk_id)" :alt="`试题 #${q.id} 的教材插图局部裁剪`" /></a></div><p>{{ Object.entries(q.options).map(([key, value]) => `${key}. ${value}`).join(' · ') }}</p><small>答案 {{ q.answer }} · {{ q.explanation }}</small></div></details>
              <div v-if="paper.status === 'draft'" class="publish-row"><label>开始<input v-model="publishStart" type="datetime-local" /></label><label>结束<input v-model="publishEnd" type="datetime-local" /></label><button class="primary" :disabled="busy" @click="publishPaper(paper.id)">核对并发布</button></div>
              <div v-if="paper.status === 'published' && paperTimeEdit === paper.id" class="publish-row"><label>开始<input v-model="paperTimeForm.starts_at" type="datetime-local" /></label><label>结束<input v-model="paperTimeForm.ends_at" type="datetime-local" /></label><button class="primary" :disabled="busy" @click="savePaperTime(paper.id)">保存时间</button><button class="secondary" @click="paperTimeEdit = null">取消</button></div>
              <div v-if="paper.status === 'published'" class="button-row"><button v-if="paperTimeEdit !== paper.id" class="secondary" @click="editPaperTime(paper)">修改时间</button><button v-if="!paperCanDelete(paper)" class="danger-btn" :disabled="busy" @click="terminatePaper(paper)">终止考试</button></div>
              <p v-if="paper.status === 'published' && !paperCanDelete(paper)" class="muted">考试结束或终止后可以删除试卷。</p>
            </div><div v-if="!visiblePapers.length" class="empty">尚无试卷。</div>
          </div>
        </section>

        <section v-if="tab === 'models' && canViewModels">
          <div class="page-head"><div><p class="eyebrow">MODEL PROVIDERS</p><h1>模型接入</h1><p>连接本机、局域网或远程 Ollama，也可使用 OpenAI 兼容提供商，并分别配置文本与视觉 OCR 模型。</p></div></div>
          <div v-if="!canManageModels" class="panel"><h2>当前模型（只读）</h2><div class="source-line"><span>文本：{{ aiStatus?.text?.provider || '未设置' }} / {{ aiStatus?.text?.model || '未设置' }}</span><span>视觉 OCR：{{ aiStatus?.vision?.provider || '未设置' }} / {{ aiStatus?.vision?.model || '未设置' }}</span></div></div><div v-if="canManageModels" class="two-col"><div class="panel form-panel"><h2>当前模型</h2>
            <div v-for="kind in (['text', 'vision'] as const)" :key="kind" class="model-config">
              <h3>{{ kind === 'text' ? '知识点与试题生成' : '扫描页视觉 OCR' }}</h3>
              <label>提供商<select v-model.number="aiConfig[`${kind}_provider_id`]" @change="aiConfig[`${kind}_model`] = ''; modelTest[kind] = null; loadProviderModels(aiConfig[`${kind}_provider_id`])"><option :value="null" disabled>请选择</option><option v-for="provider in providers" :key="provider.id" :value="provider.id">{{ provider.display_name }}</option></select></label>
              <div class="button-row"><button class="small" :disabled="!aiConfig[`${kind}_provider_id`] || providerLoading[aiConfig[`${kind}_provider_id`]]" @click="loadProviderModels(aiConfig[`${kind}_provider_id`])">{{ providerLoading[aiConfig[`${kind}_provider_id`]] ? '正在查询…' : '获取支持的模型' }}</button><span class="muted">{{ providerModels[aiConfig[`${kind}_provider_id`]]?.length || 0 }} 个模型</span></div>
              <label>模型<select v-model="aiConfig[`${kind}_model`]" @change="modelTest[kind] = null"><option value="" disabled>请选择模型</option><option v-if="aiConfig[`${kind}_model`] && !providerModels[aiConfig[`${kind}_provider_id`]]?.includes(aiConfig[`${kind}_model`])" :value="aiConfig[`${kind}_model`]">{{ aiConfig[`${kind}_model`] }}（当前配置）</option><option v-for="model in providerModels[aiConfig[`${kind}_provider_id`]] || []" :key="model" :value="model">{{ model }}</option></select></label>
              <div class="button-row"><button class="secondary" :disabled="busy || !aiConfig[`${kind}_model`]" @click="checkModel(kind)">测试可用性</button><span v-if="modelTest[kind]" class="model-ok">可用 · {{ modelTest[kind].latency_ms }} ms · {{ modelTest[kind].reply }}</span></div>
            </div>
            <button class="primary" :disabled="busy || !aiConfig.text_model || !aiConfig.vision_model" @click="saveAiConfig">保存模型选择并启用</button>
            <p class="muted">测试可用性只检查连接；需要保存后，解析和出题任务才会使用所选模型。当前启用：文本 {{ aiStatus?.text?.provider || '未设置' }} / {{ aiStatus?.text?.model || '未设置' }}，视觉 {{ aiStatus?.vision?.provider || '未设置' }} / {{ aiStatus?.vision?.model || '未设置' }}。</p>
          </div><div class="panel form-panel"><h2>{{ editingProviderId ? '编辑提供商' : '添加提供商' }}</h2><p class="muted">Ollama 可部署在本机、局域网或远程服务器；云端需提供 OpenAI 兼容的 /models 和 /chat/completions 接口。密钥保存后不再显示。</p>
            <label>显示名称<input v-model="newProvider.display_name" placeholder="如：云端 VL" /></label>
            <label>接口类型<select v-model="newProvider.api_style"><option value="openai">OpenAI 兼容</option><option value="ollama">Ollama</option></select></label>
            <template v-if="newProvider.api_style === 'ollama'">
              <div class="ollama-address-row"><label>协议<select v-model="newProvider.ollama_scheme"><option value="http">HTTP</option><option value="https">HTTPS</option></select></label><label>Ollama 主机地址<input v-model.trim="newProvider.ollama_host" placeholder="如：192.168.1.20 或 ollama.example.com" /></label><label>端口<input v-model.number="newProvider.ollama_port" type="number" min="1" max="65535" placeholder="11434" /></label></div>
              <p class="muted">后端运行在容器中时，宿主机 Ollama 请填 host.docker.internal；其他设备填写其 IP 或域名。当前连接：{{ providerBaseUrl() }}</p>
            </template>
            <label v-else>API 根地址<input v-model="newProvider.base_url" placeholder="https://api.example.com/v1" /></label>
            <label>API 密钥<input v-model="newProvider.api_key" type="password" autocomplete="new-password" :placeholder="editingProviderId ? '留空则保留原密钥' : 'Ollama 可留空'" /></label>
            <div class="button-row"><button class="secondary" :disabled="busy || (newProvider.api_style === 'ollama' ? !newProvider.ollama_host || !newProvider.ollama_port : !newProvider.base_url)" @click="testProviderConnection">测试连接</button><button class="primary" :disabled="busy || !newProvider.display_name || (newProvider.api_style === 'ollama' ? !newProvider.ollama_host || !newProvider.ollama_port : !newProvider.base_url)" @click="saveProvider">保存提供商</button><button v-if="editingProviderId" class="secondary" @click="resetProviderForm">取消编辑</button></div>
            <p v-if="providerConnectionTest" class="model-ok">连接成功 · {{ providerConnectionTest.latency_ms }} ms · 找到 {{ providerConnectionTest.model_count }} 个模型<span v-if="providerConnectionTest.models?.length">：{{ providerConnectionTest.models.slice(0, 8).join('、') }}{{ providerConnectionTest.models.length > 8 ? '…' : '' }}</span></p>
          </div></div>
          <div class="panel"><div class="panel-head"><h2>提供商列表</h2><span>{{ providers.length }} 个</span></div><div v-for="provider in providers" :key="provider.id" class="model-provider-row"><div><strong>{{ provider.display_name }}</strong><p class="muted">{{ provider.api_style === 'ollama' ? 'Ollama' : 'OpenAI 兼容' }} · {{ provider.base_url }} · {{ provider.has_api_key ? '已设置密钥' : '未设置密钥' }}</p></div><div class="button-row"><button class="small" @click="loadProviderModels(provider.id)">查询模型</button><button v-if="canManageModels" class="text-btn" @click="editProvider(provider)">编辑</button></div></div></div>
        </section>

        <section v-if="tab === 'users' && canViewUsers">
          <div class="page-head"><div><p class="eyebrow">ACCOUNT MANAGEMENT</p><h1>账号与权限</h1><p>按功能逐项授权。admin 始终拥有全部权限，教师和学生可在创建时授权并随时调整。</p></div></div>
          <div class="account-grid">
            <div v-if="canManageUsers" class="panel form-panel account-create-panel"><h2>创建账号</h2>
              <label>用户名<input v-model="newAccount.username" autocomplete="off" placeholder="3-40 位字母、数字或下划线" /></label>
              <label>初始密码<input v-model="newAccount.password" type="password" autocomplete="new-password" placeholder="至少 8 位" /></label>
              <label>角色<select v-model="newAccount.role"><option value="teacher">教师</option><option value="student">学生</option></select></label>
              <label class="inline-check"><input v-model="newAccount.must_change_password" type="checkbox" />首次登录必须修改密码</label>
              <h3>勾选初始权限</h3><p class="muted">选择管理或操作权限时，系统会自动补充其所需的查看权限。</p>
              <div class="permission-picker permission-picker-inline"><fieldset v-for="(items, group) in permissionGroups" :key="group"><legend>{{ group }}</legend><div class="permission-options"><label v-for="permission in items" :key="permission.code" class="permission-option"><input v-model="newAccount.permissions" type="checkbox" :value="permission.code" /><span><strong>{{ permission.name }}</strong><small>{{ permission.description }}</small></span></label></div></fieldset></div>
              <button class="primary" :disabled="busy || !newAccount.username || !newAccount.password" @click="createAccount">创建账号</button>
            </div>
            <div class="panel account-list"><div class="panel-head"><h2>已有账号</h2><span>{{ accounts.length }} 个</span></div>
              <button v-for="account in accounts" :key="account.id" class="account-row" :class="{ selected: editingAccount?.id === account.id }" :disabled="!canManageUsers || account.role === 'admin'" @click="editAccountPermissions(account)"><span><strong>{{ account.username }}</strong><small>{{ account.role === 'admin' ? '全部权限（固定）' : `${account.permissions?.length || 0} 项权限${account.must_change_password ? ' · 待修改初始密码' : ''}` }}</small></span><b>{{ account.role === 'admin' ? '管理员' : account.role === 'teacher' ? '教师' : '学生' }}</b></button>
            </div>
          </div>
          <Teleport to="body"><div v-if="editingAccount && canManageUsers" class="permission-modal-backdrop" @click.self="editingAccount = null"><section class="permission-modal" role="dialog" aria-modal="true" :aria-label="`修改 ${editingAccount.username} 的账号`">
            <header><div><p class="eyebrow">USER ACCOUNT</p><h2>修改 {{ editingAccount.username }}</h2><p class="muted">可修改用户名、权限或重置初始密码。保存后立即生效。</p></div><button class="modal-close" aria-label="关闭" @click="editingAccount = null">×</button></header>
            <div class="permission-modal-body">
              <section class="account-edit-section"><h3>账号信息</h3><label>用户名<input v-model="editingUsername" autocomplete="off" placeholder="3-40 位字母、数字或下划线" /></label></section>
              <div class="permission-picker permission-picker-inline"><fieldset v-for="(items, group) in permissionGroups" :key="group"><legend>{{ group }}</legend><div class="permission-options"><label v-for="permission in items" :key="permission.code" class="permission-option"><input v-model="editingPermissions" type="checkbox" :value="permission.code" /><span><strong>{{ permission.name }}</strong><small>{{ permission.description }}</small></span></label></div></fieldset></div>
              <section class="password-reset-section"><h3>重置初始密码</h3><p class="muted">重置后，该用户当前所有登录状态立即失效。</p><div class="form-row"><label>新初始密码<input v-model="resetPassword" type="password" autocomplete="new-password" placeholder="至少 8 位" /></label><label>确认初始密码<input v-model="resetPasswordConfirm" type="password" autocomplete="new-password" /></label></div><label class="inline-check"><input v-model="resetMustChange" type="checkbox" />登录后必须修改密码</label><button class="danger-btn" :disabled="busy || editingAccount.id === user.id" @click="resetAccountPassword">重置初始密码</button><p v-if="editingAccount.id === user.id" class="muted">当前登录账号请通过个人修改密码流程处理。</p></section>
            </div>
            <footer><button class="secondary" :disabled="busy" @click="editingAccount = null">取消</button><button class="primary" :disabled="busy" @click="saveAccountPermissions">保存账号与权限</button></footer>
          </section></div></Teleport>
        </section>

        <section v-if="tab === 'jobs' && canViewJobs"><div class="page-head"><div><p class="eyebrow">TASK MONITOR</p><h1>后台任务</h1><p>解析、知识点提取和出题均由队列处理。已结束任务可从列表清空，历史记录留存。</p></div><div class="action-strip"><button class="secondary" :disabled="busy" @click="refresh">刷新</button><button v-if="canManageJobs" class="secondary" :disabled="busy" @click="clearFinishedJobs">清空已结束任务</button></div></div><div class="panel"><div v-for="job in jobs" :key="job.id" class="job-row"><div><strong>#{{ job.id }} · {{ job.kind === 'toc' ? '目录解析' : job.kind === 'parse' ? '章节正文解析' : job.kind === 'knowledge' ? '本章知识点总结' : job.kind === 'chapter_questions' ? '本章出题' : 'AI 出题' }}</strong><p>{{ job.error || `目标 #${job.target_id}` }}</p></div><div class="job-progress"><div class="progress-track"><i :style="{ width: `${job.total ? job.progress / job.total * 100 : 0}%` }"></i></div><small>{{ job.progress }} / {{ job.total }}</small></div><span class="badge" :class="job.status">{{ statusName(job.status) }}</span><button v-if="canManageJobs && ['queued','running','retrying'].includes(job.status)" class="small" @click="cancelJob(job.id)">取消</button><button v-if="canManageJobs && ['failed','cancelled'].includes(job.status)" class="small" @click="retryJob(job.id)">重试</button></div><div v-if="!jobs.length" class="empty">暂无任务。</div></div></section>

        <section v-if="tab === 'exam' && canExam"><div class="page-head"><div><p class="eyebrow">TAKE AN EXAM</p><h1>参加考试</h1><p>服务端计时与自动保存；每场考试只能交卷一次。</p></div><strong v-if="currentAttempt" class="countdown">剩余 {{ remaining }}</strong></div><div v-if="currentAttempt" class="panel exam-panel"><div v-for="(q, index) in currentAttempt.questions" :key="q.id" class="exam-question"><h3>{{ index + 1 }}. {{ q.stem }} <small>{{ q.score }} 分</small></h3><div v-if="q.image_pdf_page && currentAttempt.book_id" class="figure-preview"><span>教材插图局部裁剪</span><a v-if="sourceImageUrl(currentAttempt.book_id, q.image_pdf_page, q.image_chunk_id)" :href="sourceImageUrl(currentAttempt.book_id, q.image_pdf_page, q.image_chunk_id)" target="_blank" rel="noopener"><img :src="sourceImageUrl(currentAttempt.book_id, q.image_pdf_page, q.image_chunk_id)" :alt="`第 ${index + 1} 题的教材图片`" /></a><p v-else class="muted">{{ sourceImageError(currentAttempt.book_id, q.image_pdf_page, q.image_chunk_id) || '图片加载中…' }}</p></div><label v-for="key in ['A','B','C','D']" :key="key" class="exam-option"><input type="radio" :name="`q-${q.id}`" :checked="currentAttempt.answers[q.id] === key" @change="saveAnswer(q.id, key)" /><span>{{ key }}. {{ q.options[key] }}</span></label></div><button class="primary" @click="submitExam">提交试卷</button></div><div v-else class="card-grid"><div v-for="paper in papers" :key="paper.id" class="panel exam-card"><span class="badge published">已发布</span><h2>{{ paper.title }}</h2><p>总分 {{ paper.total_score }} 分</p><p class="muted">{{ time(paper.starts_at) }} — {{ time(paper.ends_at) }}</p><button class="primary" @click="startExam(paper.id)">进入考试</button></div><div v-if="!papers.length" class="panel empty">暂无已发布考试。</div></div></section>

        <section v-if="tab === 'results' && canResults"><div class="page-head"><div><p class="eyebrow">REVIEW & PRACTICE</p><h1>成绩与错题</h1><p>回到原文，优先用已审核原题重练；变式题审核通过后开放。</p></div></div><div class="two-col"><div class="panel"><h2>我的答卷</h2><button v-for="a in attempts" :key="a.id" class="attempt-row" @click="openResult(a.id)"><span><strong>{{ a.paper_title }}</strong><small>{{ time(a.submitted_at || a.started_at) }}</small></span><b>{{ a.score === null ? '进行中' : `${a.score} 分` }}</b></button><div v-if="!attempts.length" class="empty">还没有答卷。</div></div><div class="panel"><h2>知识点掌握</h2><div v-for="(m, id) in mastery" :key="id" class="mastery-row"><span>{{ m.title || `知识点 #${id}` }}</span><strong>{{ m.correct }} / {{ m.total }}</strong></div><div v-if="!Object.keys(mastery).length" class="empty">交卷后显示掌握情况。</div></div></div><div v-if="variantRequests.length" class="panel"><h2>变式题请求</h2><div v-for="request in variantRequests" :key="request.original_id" class="mastery-row"><span>原题 #{{ request.original_id }} · {{ request.error || (request.question_status === 'approved' ? '可在错题处开始练习' : request.question_status ? '等待审核员确认' : '正在生成') }}</span><strong>{{ statusName(request.question_status || request.job_status) }}</strong></div></div><div v-if="result?.submitted_at" class="panel"><div class="panel-head"><h2>本次成绩：{{ result.score }} 分</h2><span>{{ wrongRows.length }} 道错题</span></div><div v-for="row in wrongRows" :key="row.question_id" class="record"><strong>{{ row.stem }}</strong><div v-if="row.image_pdf_page && result.book_id" class="figure-preview"><span>教材插图局部裁剪</span><a v-if="sourceImageUrl(result.book_id, row.image_pdf_page, row.image_chunk_id)" :href="sourceImageUrl(result.book_id, row.image_pdf_page, row.image_chunk_id)" target="_blank" rel="noopener"><img :src="sourceImageUrl(result.book_id, row.image_pdf_page, row.image_chunk_id)" alt="错题对应的教材插图局部裁剪" /></a><p v-else class="muted">{{ sourceImageError(result.book_id, row.image_pdf_page, row.image_chunk_id) || "图片加载中…" }}</p></div><p>你的答案：{{ row.answer || '未作答' }} · 正确答案：{{ row.correct_answer }}</p><p>{{ row.explanation }}</p><blockquote>{{ row.evidence }}</blockquote><div v-if="canPractice" class="button-row"><button class="secondary" @click="startPractice(row, 'original')">原题重练</button><button class="secondary" @click="startPractice(row, 'variant')">同知识点变式</button></div></div><div v-if="!wrongRows.length" class="empty">本次没有错题。</div></div><div v-if="practice" class="panel practice-panel"><h2>{{ practice.practice.mode === 'variant' ? '变式练习' : '原题重练' }}</h2><h3>{{ practice.question.stem }}</h3><div v-if="practice.question.image_pdf_page && practice.question.book_id" class="figure-preview"><span>教材插图局部裁剪</span><a v-if="sourceImageUrl(practice.question.book_id, practice.question.image_pdf_page, practice.question.image_chunk_id)" :href="sourceImageUrl(practice.question.book_id, practice.question.image_pdf_page, practice.question.image_chunk_id)" target="_blank" rel="noopener"><img :src="sourceImageUrl(practice.question.book_id, practice.question.image_pdf_page, practice.question.image_chunk_id)" alt="练习题对应的教材插图局部裁剪" /></a><p v-else class="muted">{{ sourceImageError(practice.question.book_id, practice.question.image_pdf_page, practice.question.image_chunk_id) || "图片加载中…" }}</p></div><label v-for="key in ['A','B','C','D']" :key="key" class="exam-option"><input v-model="practice.selected" type="radio" :value="key" :disabled="!!practice.feedback" /><span>{{ key }}. {{ practice.question.options[key] }}</span></label><button v-if="!practice.feedback" class="primary" @click="answerPractice">提交练习</button><div v-else class="feedback"><strong>{{ practice.feedback.correct ? '回答正确' : `正确答案：${practice.feedback.answer}` }}</strong><p>{{ practice.feedback.explanation }}</p><blockquote>{{ practice.feedback.evidence }}</blockquote></div></div></section>
      </div>
    </div>
  </div>
</template>

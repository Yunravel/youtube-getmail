import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// 响应拦截:统一错误提示
api.interceptors.response.use(
  (resp) => resp.data,
  (err) => {
    const msg = err.response?.data?.detail || err.message || '请求失败'
    console.error('[API Error]', msg)
    return Promise.reject(err)
  }
)

// ===== KOL =====
export const kolApi = {
  list: (params) => api.get('/kols', { params }),
  filterOptions: () => api.get('/kols/filters/options'),
  get: (id) => api.get(`/kols/${id}`),
  importCsv: (file) => {
    const form = new FormData()
    form.append('file', file)
    return api.post('/kols/import-csv', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  // 导入 KOL-Find 候选池 Excel → kol_candidate 大数据库 + 选入 kol/kol_email
  importCandidate: (file) => {
    const form = new FormData()
    form.append('file', file)
    return api.post('/kols/import-candidate', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,  // 大文件 5103 行，给 2 分钟
    })
  },
  delete: (id) => api.delete(`/kols/${id}`),
  generateIntros: (kolIds, ourProduct = 'our product') =>
    api.post('/kols/generate-intros', { kol_ids: kolIds, our_product: ourProduct }),
}

// ===== 会话 =====
export const threadApi = {
  list: (params) => api.get('/threads', { params }),
  detail: (id) => api.get(`/threads/${id}`),
  assign: (id, assigneeId) => api.post(`/threads/${id}/assign`, { assignee_id: assigneeId }),
  updateStatus: (id, status) => api.post(`/threads/${id}/status`, { status }),
  addNote: (id, operatorId, content) =>
    api.post(`/threads/${id}/notes`, { operator_id: operatorId, content }),
  // AI 画像回填（HotLead 看板）：thread_ids 为空则处理全部 hot
  backfillProfile: (threadIds = null, force = false) =>
    api.post('/threads/backfill-profile', { thread_ids: threadIds, force }),
  backfillStatus: () => api.get('/threads/backfill-status'),
  // 报价单导出：返回 xlsx blob，前端触发下载
  exportQuotes: (threadIds, project = 'dola') =>
    api.post('/threads/export', { thread_ids: threadIds, project }, { responseType: 'blob' }),
}

// ===== 邮箱 =====
export const mailboxApi = {
  list: (params) => api.get('/mailbox', { params }),
  filters: () => api.get('/mailbox/filters'),
  updateThread: (id, state) => api.patch(`/mailbox/threads/${id}`, state),
  updateThreads: (threadIds, state) =>
    api.patch('/mailbox/threads', { thread_ids: threadIds, ...state }),
}

// ===== 运营人员 =====
export const operatorApi = {
  list: () => api.get('/operators'),
}

// ===== 统计 =====
export const statsApi = {
  overview: () => api.get('/stats/overview'),
  intentDistribution: () => api.get('/stats/intent-distribution'),
  campaignProjects: () => api.get('/stats/campaign-projects'),
  campaignRates: (project = 'all') =>
    api.get('/stats/campaign-rates', { params: { project } }),
}

// ===== 营销活动与待发送名单 =====
export const campaignApi = {
  campaigns: () => api.get('/snov/campaigns'),
  createProspectListFromKols: (listName, kolIds) =>
    api.post('/snov/prospect-lists/from-kols', {
      list_name: listName,
      kol_ids: kolIds,
    }, { timeout: 120000 }),
}

// ===== 采集器（KOL 爬虫）=====
// 触发后台采集任务 + 轮询进度，复刻 threadApi 的回填任务模式
// 采集端点要求 X-Crawler-Token 头（与后端 CRAWLER_TOKEN 一致），构建期注入。
const CRAWLER_TOKEN_HEADERS = (() => {
  const token = import.meta.env.VITE_CRAWLER_TOKEN
  return token ? { 'X-Crawler-Token': token } : {}
})()

export const crawlerApi = {
  // 可选产品列表 + 关键词数（渲染复选框）
  products: () => api.get('/crawler/products', { headers: CRAWLER_TOKEN_HEADERS }),
  createProduct: (name, keywords) =>
    api.post('/crawler/products', { name, keywords }, { headers: CRAWLER_TOKEN_HEADERS }),
  updateProduct: (id, name, keywords) =>
    api.put(`/crawler/products/${id}`, { name, keywords }, { headers: CRAWLER_TOKEN_HEADERS }),
  deleteProduct: (id) =>
    api.delete(`/crawler/products/${id}`, { headers: CRAWLER_TOKEN_HEADERS }),
  // 触发采集（后台任务），立即返回 job_id
  // products 和 customQueries 至少有一个非空
  start: (products, {
    enableEnrich = true, enableEmail = true, enableDeepEmail = false,
    enableScrapeCreators = false, customQueries = [],
  } = {}) =>
    api.post('/crawler',
      { products, enable_enrich: enableEnrich, enable_email: enableEmail,
        enable_deep_email: enableDeepEmail,
        enable_scrape_creators: enableScrapeCreators,
        custom_queries: customQueries },
      { headers: CRAWLER_TOKEN_HEADERS }),
  // 查询最新采集任务进度（前端定时轮询）
  status: () => api.get('/crawler/status', { headers: CRAWLER_TOKEN_HEADERS }),
}

// ===== 邮箱凭据管理（IMAP 附件同步用）=====
export const mailboxCredentialApi = {
  list: () => api.get('/mailbox-credentials'),
  status: () => api.get('/mailbox-credentials/status'),
  create: (data) => api.post('/mailbox-credentials', data),
  update: (id, data) => api.put(`/mailbox-credentials/${id}`, data),
  remove: (id) => api.delete(`/mailbox-credentials/${id}`),
  // 从发信平台批量导入已配置的发信邮箱（密码留空待填）
  importFromProvider: () => api.post('/mailbox-credentials/import-from-provider'),
  testSmtp: (id) => api.post(`/mailbox-credentials/${id}/test-smtp`),
}

// ===== 报价自动回复 =====
export const autoReplyApi = {
  templates: () => api.get('/auto-replies/templates'),
  saveTemplate: (data) => api.put('/auto-replies/templates', data),
  previewTemplate: (data) => api.post('/auto-replies/templates/preview', data),
  setEnabled: (enabled) => api.put('/auto-replies/settings', { enabled }),
  threadTask: (threadId) => api.get(`/auto-replies/threads/${threadId}/task`),
  createManual: (threadId, subject, body, { scheduledAt = null, sendNow = false } = {}) =>
    api.post(`/auto-replies/threads/${threadId}/manual`, {
      subject, body, scheduled_at: scheduledAt, send_now: sendNow,
    }),
  updateDraft: (taskId, subject, body) =>
    api.patch(`/auto-replies/tasks/${taskId}`, { subject, body }),
  cancelTask: (taskId) => api.post(`/auto-replies/tasks/${taskId}/cancel`),
  scheduleTask: (taskId, scheduledAt) =>
    api.post(`/auto-replies/tasks/${taskId}/schedule`, { scheduled_at: scheduledAt }),
  sendNow: (taskId) => api.post(`/auto-replies/tasks/${taskId}/send-now`),
}

// ===== 附件同步与下载 =====
export const attachmentApi = {
  // 手动触发同步，since_days=null/0 表示全部历史；返回 job_id
  sync: (sinceDays = 30) => api.post('/attachments/sync', { since_days: sinceDays }),
  // 查同步进度（传 job_id 查指定；不传查最近一个）
  syncStatus: (jobId = null) =>
    api.get('/attachments/sync/status', { params: jobId ? { job_id: jobId } : {} }),
  // 本地附件下载 URL（直接拼，不走 axios）
  downloadUrl: (messageId, filename) =>
    `/api/attachments/download/${messageId}/${encodeURIComponent(filename)}`,
}

export default api

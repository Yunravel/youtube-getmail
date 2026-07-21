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
}

// ===== Snov 营销活动 =====
export const snovApi = {
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
  // 触发采集（后台任务），立即返回 job_id
  // products 和 customQueries 至少有一个非空
  start: (products, {
    enableEnrich = true, enableEmail = true, enableDeepEmail = false, customQueries = [],
  } = {}) =>
    api.post('/crawler',
      { products, enable_enrich: enableEnrich, enable_email: enableEmail,
        enable_deep_email: enableDeepEmail, custom_queries: customQueries },
      { headers: CRAWLER_TOKEN_HEADERS }),
  // 查询最新采集任务进度（前端定时轮询）
  status: () => api.get('/crawler/status', { headers: CRAWLER_TOKEN_HEADERS }),
}

export default api

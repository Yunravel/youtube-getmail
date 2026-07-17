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
}

export default api

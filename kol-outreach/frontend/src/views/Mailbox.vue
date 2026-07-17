<template>
  <div class="mailbox-page">
    <header class="mailbox-toolbar">
      <a-tooltip :title="composeUrl ? '前往 Snov 新建邮件' : '请配置 VITE_SNOV_INBOX_URL'">
        <span class="compose-wrap">
          <a-button
            class="compose-button"
            type="primary"
            :disabled="!composeUrl"
            @click="openCompose"
          >
            <template #icon><FormOutlined /></template>
            新邮件
          </a-button>
        </span>
      </a-tooltip>

      <a-input-search
        v-model:value="searchText"
        class="mail-search"
        allow-clear
        placeholder="搜索联系人、邮箱、主题或正文"
        @search="applySearch"
      />

      <a-select
        v-model:value="campaignId"
        class="mail-filter"
        allow-clear
        placeholder="所有营销任务"
        @change="changeFilter"
      >
        <a-select-option
          v-for="campaign in filters.campaigns"
          :key="campaign.id"
          :value="campaign.id"
        >
          {{ campaign.name }}
        </a-select-option>
      </a-select>

      <a-select
        v-model:value="account"
        class="mail-filter account-filter"
        allow-clear
        placeholder="所有收发邮箱"
        @change="changeFilter"
      >
        <a-select-option v-for="email in filters.accounts" :key="email" :value="email">
          {{ email }}
        </a-select-option>
      </a-select>

      <a-button :loading="refreshing" @click="loadMailbox({ preserveSelection: true })">
        <template #icon><ReloadOutlined /></template>
        刷新
      </a-button>
    </header>

    <div class="mailbox-body">
      <aside class="mailbox-folders" aria-label="邮箱文件夹">
        <button
          v-for="item in folderOptions"
          :key="item.key"
          type="button"
          class="folder-button"
          :class="{ active: folder === item.key }"
          @click="changeFolder(item.key)"
        >
          <span class="folder-label">
            <component :is="item.icon" />
            {{ item.label }}
          </span>
          <span class="folder-count">{{ folderCounts[item.key] || 0 }}</span>
        </button>

        <div class="sync-note">
          <ClockCircleOutlined />
          <span>每 2 分钟自动同步</span>
          <small v-if="lastUpdated">更新于 {{ lastUpdated }}</small>
        </div>
      </aside>

      <main class="mailbox-list-panel">
        <div class="list-actions">
          <div class="bulk-actions">
            <a-checkbox
              :checked="allCurrentSelected"
              :indeterminate="selectionIndeterminate"
              aria-label="选择当前页全部会话"
              @change="toggleSelectAll"
            />
            <template v-if="selectedIds.size">
              <span class="selected-count">已选 {{ selectedIds.size }} 项</span>
              <a-button size="small" @click="bulkUpdate({ is_read: true })">
                <template #icon><MailOutlined /></template>
                标为已读
              </a-button>
              <a-button size="small" @click="bulkUpdate({ is_read: false })">
                标为未读
              </a-button>
              <a-button size="small" @click="bulkUpdate({ is_starred: true })">
                <template #icon><StarOutlined /></template>
                加星标
              </a-button>
              <a-button size="small" @click="bulkUpdate({ is_starred: false })">
                取消星标
              </a-button>
            </template>
            <span v-else class="list-status">
              {{ activeFolderLabel }} · {{ total }} 个会话
            </span>
          </div>

          <span class="page-range">{{ pageRange }}</span>
        </div>

        <a-spin :spinning="loading">
          <div v-if="items.length" class="conversation-list">
            <article
              v-for="item in items"
              :key="item.thread_id"
              class="conversation-row"
              :class="{ unread: !item.is_read, selected: selectedIds.has(item.thread_id) }"
              tabindex="0"
              role="button"
              :aria-label="`查看 ${item.contact_name || item.contact_email} 的邮件会话`"
              @click="openThread(item)"
              @keydown.enter="openThread(item)"
            >
              <div class="check-cell" @click.stop>
                <a-checkbox
                  :checked="selectedIds.has(item.thread_id)"
                  :aria-label="`选择 ${item.contact_name || item.contact_email}`"
                  @change="toggleSelection(item.thread_id, $event.target.checked)"
                />
              </div>

              <button
                type="button"
                class="star-button"
                :class="{ starred: item.is_starred }"
                :aria-label="item.is_starred ? '取消星标' : '添加星标'"
                @click.stop="toggleStar(item)"
              >
                <StarFilled v-if="item.is_starred" />
                <StarOutlined v-else />
              </button>

              <div class="contact-cell">
                <span class="mail-avatar"><MailOutlined /></span>
                <div class="contact-copy">
                  <span class="contact-name">{{ item.contact_name || '未命名联系人' }}</span>
                  <span class="contact-email">{{ item.contact_email }}</span>
                </div>
                <span v-if="item.message_count > 1" class="message-count">
                  {{ item.message_count }}
                </span>
              </div>

              <div class="subject-cell">
                <span class="subject-text">{{ item.subject || '无主题' }}</span>
                <span v-if="item.preview" class="preview-dot">•</span>
                <span class="preview-text">{{ item.preview || '暂无正文预览' }}</span>
              </div>

              <div class="campaign-cell">
                <a-tag v-if="item.campaign_name || item.campaign_id" color="blue">
                  {{ item.campaign_name || item.campaign_id }}
                </a-tag>
                <span v-else>—</span>
              </div>

              <PaperClipOutlined
                v-if="item.has_attachments"
                class="attachment-cell"
                title="包含附件"
              />
              <span v-else class="attachment-cell" />

              <time class="time-cell" :title="fullTime(item.last_message_at)">
                {{ formatTime(item.last_message_at) }}
              </time>
            </article>
          </div>

          <a-empty
            v-else-if="!loading"
            class="mailbox-empty"
            :description="emptyDescription"
          />
        </a-spin>

        <footer v-if="total > pageSize" class="pagination-wrap">
          <a-pagination
            :current="page"
            :page-size="pageSize"
            :total="total"
            :show-size-changer="false"
            @change="changePage"
          />
        </footer>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, markRaw, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import dayjs from 'dayjs'
import {
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  FormOutlined,
  InboxOutlined,
  MailOutlined,
  PaperClipOutlined,
  ReloadOutlined,
  SendOutlined,
  StarFilled,
  StarOutlined,
} from '@ant-design/icons-vue'
import { mailboxApi } from '../api'

const router = useRouter()
const composeUrl = (() => {
  const value = import.meta.env.VITE_SNOV_INBOX_URL || ''
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) ? url.toString() : ''
  } catch {
    return ''
  }
})()
const pageSize = 25
const items = ref([])
const total = ref(0)
const page = ref(1)
const folder = ref('inbox')
const folderCounts = ref({ inbox: 0, sent: 0, starred: 0, bounced: 0 })
const filters = ref({ campaigns: [], accounts: [] })
const campaignId = ref(undefined)
const account = ref(undefined)
const searchText = ref('')
const activeSearch = ref('')
const selectedIds = ref(new Set())
const loading = ref(false)
const refreshing = ref(false)
const lastUpdated = ref('')
let refreshTimer = null

const folderOptions = [
  { key: 'inbox', label: '收件箱', icon: markRaw(InboxOutlined) },
  { key: 'sent', label: '已发送', icon: markRaw(SendOutlined) },
  { key: 'starred', label: '已加星标', icon: markRaw(StarOutlined) },
  { key: 'bounced', label: '退信', icon: markRaw(ExclamationCircleOutlined) },
]

const activeFolderLabel = computed(() =>
  folderOptions.find((item) => item.key === folder.value)?.label || '邮箱'
)

const allCurrentSelected = computed(() =>
  items.value.length > 0 && items.value.every((item) => selectedIds.value.has(item.thread_id))
)

const selectionIndeterminate = computed(() => {
  const selectedOnPage = items.value.filter((item) => selectedIds.value.has(item.thread_id)).length
  return selectedOnPage > 0 && selectedOnPage < items.value.length
})

const pageRange = computed(() => {
  if (!total.value) return '0 条'
  const start = (page.value - 1) * pageSize + 1
  const end = Math.min(page.value * pageSize, total.value)
  return `${start} - ${end} / ${total.value}`
})

const emptyDescription = computed(() => {
  if (activeSearch.value || campaignId.value || account.value) return '没有符合筛选条件的会话'
  const labels = {
    inbox: '暂时没有收到邮件',
    sent: '尚未同步到真实已发送邮件',
    starred: '还没有加星标的会话',
    bounced: '当前没有退信记录',
  }
  return labels[folder.value]
})

async function loadMailbox({ silent = false, preserveSelection = false } = {}) {
  if (silent) refreshing.value = true
  else loading.value = true
  try {
    const result = await mailboxApi.list({
      folder: folder.value,
      q: activeSearch.value || undefined,
      campaign_id: campaignId.value,
      account: account.value,
      page: page.value,
      page_size: pageSize,
    })

    const lastPage = Math.max(1, Math.ceil(result.total / pageSize))
    if (page.value > lastPage) {
      page.value = lastPage
      return loadMailbox({ silent, preserveSelection })
    }

    items.value = result.items
    total.value = result.total
    folderCounts.value = result.folder_counts
    lastUpdated.value = dayjs().format('HH:mm:ss')
    if (!preserveSelection) selectedIds.value = new Set()
  } catch (error) {
    message.error(error.response?.data?.detail || '邮箱刷新失败，请稍后重试')
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function loadFilters() {
  try {
    filters.value = await mailboxApi.filters()
  } catch {
    message.warning('邮箱筛选项加载失败，邮件列表仍可正常使用')
  }
}

function changeFolder(nextFolder) {
  if (nextFolder === folder.value) return
  folder.value = nextFolder
  page.value = 1
  loadMailbox()
}

function changeFilter() {
  page.value = 1
  loadMailbox()
}

function applySearch(value) {
  activeSearch.value = (value || '').trim()
  page.value = 1
  loadMailbox()
}

function changePage(nextPage) {
  page.value = nextPage
  loadMailbox()
}

function toggleSelection(threadId, checked) {
  const next = new Set(selectedIds.value)
  if (checked) next.add(threadId)
  else next.delete(threadId)
  selectedIds.value = next
}

function toggleSelectAll(event) {
  const next = new Set(selectedIds.value)
  for (const item of items.value) {
    if (event.target.checked) next.add(item.thread_id)
    else next.delete(item.thread_id)
  }
  selectedIds.value = next
}

async function bulkUpdate(state) {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  try {
    await mailboxApi.updateThreads(ids, state)
    message.success(`已更新 ${ids.length} 个会话`)
    selectedIds.value = new Set()
    await loadMailbox({ silent: true })
  } catch (error) {
    message.error(error.response?.data?.detail || '批量更新失败')
  }
}

async function toggleStar(item) {
  const previous = item.is_starred
  item.is_starred = !previous
  try {
    await mailboxApi.updateThread(item.thread_id, { is_starred: item.is_starred })
    await loadMailbox({ silent: true, preserveSelection: true })
  } catch (error) {
    item.is_starred = previous
    message.error(error.response?.data?.detail || '星标更新失败')
  }
}

async function openThread(item) {
  if (!item.is_read) {
    item.is_read = true
    item.unread_count = 0
    try {
      await mailboxApi.updateThread(item.thread_id, { is_read: true })
    } catch {
      // Navigation remains useful even if the read-state update temporarily fails.
    }
  }
  router.push({ name: 'ThreadDetail', params: { id: item.thread_id }, query: { from: 'mailbox' } })
}

function openCompose() {
  if (composeUrl) window.open(composeUrl, '_blank', 'noopener,noreferrer')
}

function formatTime(value) {
  if (!value) return '—'
  const date = dayjs(value)
  const now = dayjs()
  if (date.isSame(now, 'day')) return date.format('HH:mm')
  if (date.isSame(now, 'year')) return date.format('M月D日')
  return date.format('YYYY-MM-DD')
}

function fullTime(value) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : ''
}

onMounted(async () => {
  await Promise.all([loadMailbox(), loadFilters()])
  refreshTimer = window.setInterval(() => {
    loadMailbox({ silent: true, preserveSelection: true })
  }, 120000)
})

onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
})
</script>

<style scoped>
.mailbox-page {
  min-height: calc(100vh - 96px);
  color: #1f2937;
  background: #f4f7fb;
}

.mailbox-toolbar {
  display: grid;
  grid-template-columns: 180px minmax(260px, 1fr) minmax(180px, 240px) minmax(190px, 260px) auto;
  gap: 12px;
  align-items: center;
  padding: 16px;
  background: #fff;
  border-bottom: 1px solid #e8edf4;
}

.compose-wrap,
.compose-button,
.mail-search,
.mail-filter {
  width: 100%;
}

.mailbox-body {
  display: flex;
  gap: 16px;
  min-height: calc(100vh - 161px);
  padding: 16px;
}

.mailbox-folders {
  display: flex;
  flex: 0 0 196px;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  background: #fff;
  border: 1px solid #e8edf4;
  border-radius: 12px;
}

.folder-button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-height: 44px;
  padding: 0 12px;
  color: #4b5563;
  font: inherit;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: 8px;
  transition: background 0.2s, color 0.2s;
}

.folder-button:hover {
  color: #1677ff;
  background: #f2f7ff;
}

.folder-button.active {
  color: #0958d9;
  font-weight: 600;
  background: #e6f4ff;
}

.folder-label {
  display: flex;
  gap: 10px;
  align-items: center;
}

.folder-count {
  min-width: 24px;
  text-align: right;
}

.sync-note {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: center;
  margin-top: auto;
  padding: 14px 8px 4px;
  color: #64748b;
  font-size: 12px;
  text-align: center;
  border-top: 1px solid #eef2f7;
}

.sync-note :deep(.anticon) {
  color: #1677ff;
  font-size: 18px;
}

.sync-note small {
  color: #94a3b8;
}

.mailbox-list-panel {
  min-width: 0;
  overflow: hidden;
  flex: 1;
  background: #fff;
  border: 1px solid #e8edf4;
  border-radius: 12px;
}

.list-actions {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  min-height: 56px;
  padding: 8px 16px;
  border-bottom: 1px solid #edf1f5;
}

.bulk-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
  flex-wrap: wrap;
}

.selected-count,
.list-status,
.page-range {
  color: #64748b;
  font-size: 13px;
}

.page-range {
  white-space: nowrap;
}

.conversation-list {
  min-height: 260px;
}

.conversation-row {
  display: grid;
  grid-template-columns: 32px 34px minmax(170px, 230px) minmax(260px, 1fr) minmax(130px, 190px) 28px 84px;
  gap: 8px;
  align-items: center;
  min-height: 58px;
  padding: 0 14px;
  cursor: pointer;
  border-bottom: 1px solid #edf1f5;
  outline: none;
  transition: background 0.15s, box-shadow 0.15s;
}

.conversation-row:hover,
.conversation-row:focus-visible {
  z-index: 1;
  background: #f6faff;
  box-shadow: inset 3px 0 #69b1ff;
}

.conversation-row.unread {
  background: #f0f7ff;
}

.conversation-row.selected {
  background: #e6f4ff;
}

.check-cell {
  display: flex;
  align-items: center;
  justify-content: center;
}

.star-button {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 6px;
  color: #9ca3af;
  font-size: 17px;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: 50%;
}

.star-button:hover,
.star-button.starred {
  color: #faad14;
  background: #fffbe6;
}

.contact-cell,
.subject-cell {
  display: flex;
  min-width: 0;
  align-items: center;
}

.contact-cell {
  gap: 8px;
}

.mail-avatar {
  display: inline-flex;
  flex: 0 0 28px;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  color: #fff;
  background: #1677ff;
  border-radius: 8px;
}

.contact-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.contact-name,
.contact-email,
.subject-text,
.preview-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.contact-name {
  font-size: 14px;
}

.contact-email {
  color: #94a3b8;
  font-size: 11px;
}

.message-count {
  color: #64748b;
  font-size: 12px;
}

.subject-cell {
  gap: 8px;
  font-size: 14px;
}

.subject-text {
  max-width: 52%;
  flex: 0 1 auto;
}

.conversation-row.unread .contact-name,
.conversation-row.unread .subject-text {
  color: #172554;
  font-weight: 650;
}

.preview-dot,
.preview-text {
  color: #7c8798;
}

.preview-text {
  min-width: 0;
  flex: 1;
}

.campaign-cell {
  min-width: 0;
  overflow: hidden;
  color: #94a3b8;
  text-align: right;
  white-space: nowrap;
}

.campaign-cell :deep(.ant-tag) {
  max-width: 100%;
  margin-inline-end: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: middle;
}

.attachment-cell {
  color: #64748b;
  text-align: center;
}

.time-cell {
  color: #64748b;
  font-size: 13px;
  text-align: right;
  white-space: nowrap;
}

.mailbox-empty {
  margin: 90px 0;
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  padding: 16px;
  border-top: 1px solid #edf1f5;
}

@media (max-width: 1180px) {
  .mailbox-toolbar {
    grid-template-columns: 160px minmax(240px, 1fr) minmax(170px, 220px) auto;
  }

  .account-filter {
    display: none;
  }

  .conversation-row {
    grid-template-columns: 32px 34px minmax(160px, 210px) minmax(240px, 1fr) 84px;
  }

  .campaign-cell,
  .attachment-cell {
    display: none;
  }
}

@media (max-width: 820px) {
  .mailbox-toolbar {
    grid-template-columns: 1fr 1fr;
  }

  .mail-search {
    grid-column: 1 / -1;
    grid-row: 1;
  }

  .mailbox-body {
    flex-direction: column;
    padding: 8px;
  }

  .mailbox-folders {
    display: grid;
    flex: none;
    width: auto;
    padding: 8px;
    grid-template-columns: repeat(4, minmax(100px, 1fr));
    overflow-x: auto;
  }

  .sync-note {
    display: none;
  }

  .folder-button {
    min-width: 100px;
  }

  .conversation-row {
    grid-template-columns: 28px 30px minmax(120px, 170px) minmax(180px, 1fr) 70px;
    padding: 0 8px;
  }

  .contact-email,
  .preview-dot,
  .preview-text {
    display: none;
  }

  .subject-text {
    max-width: 100%;
  }
}

@media (max-width: 560px) {
  .mailbox-toolbar {
    display: flex;
    flex-wrap: wrap;
  }

  .compose-wrap,
  .mail-filter,
  .mailbox-toolbar > :deep(.ant-btn) {
    width: calc(50% - 6px);
  }

  .mail-search {
    width: 100%;
    order: -1;
  }

  .mailbox-folders {
    grid-template-columns: repeat(4, 120px);
  }

  .conversation-row {
    grid-template-columns: 24px 28px minmax(100px, 1fr) 70px;
  }

  .subject-cell {
    display: none;
  }

  .list-actions {
    align-items: flex-start;
  }
}
</style>

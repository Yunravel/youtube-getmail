<template>
  <div class="lead-list-page">
    <div class="lead-toolbar">
      <a-space :size="12" wrap>
        <a-radio-group v-model:value="filter" button-style="solid" @change="load">
          <a-radio-button value="hot">Hot Lead</a-radio-button>
          <a-radio-button value="open">待回复</a-radio-button>
          <a-radio-button value="unassigned">未分配</a-radio-button>
          <a-radio-button value="">全部</a-radio-button>
        </a-radio-group>

        <a-select
          v-model:value="campaignId"
          class="campaign-filter"
          allow-clear
          placeholder="全部营销活动"
          @change="load"
        >
          <a-select-option v-for="campaign in campaigns" :key="campaign.id" :value="campaign.id">
            {{ campaign.name || `活动 #${campaign.id}` }}
          </a-select-option>
        </a-select>

        <a-button @click="load"><ReloadOutlined /> 刷新</a-button>
      </a-space>

      <a-space :size="8" class="lead-actions">
        <span class="lead-count">{{ data.length }} 条 · 已选 {{ selectedIds.length }}</span>
        <a-checkbox
          :checked="allSelected"
          :indeterminate="someSelected && !allSelected"
          @change="toggleSelectAll"
        >
          全选
        </a-checkbox>
        <a-button
          :loading="backfilling"
          :disabled="backfilling"
          @click="startBackfill"
        >
          <ThunderboltOutlined /> AI 补全画像
        </a-button>
        <a-button
          type="primary"
          :disabled="selectedIds.length === 0 || exporting"
          :loading="exporting"
          @click="exportSelected"
        >
          <DownloadOutlined /> 导出选中 ({{ selectedIds.length }})
        </a-button>
      </a-space>
    </div>

    <a-alert
      v-if="backfilling"
      class="backfill-progress"
      type="info"
      show-icon
      :message="`AI 补全画像中… ${backfillProgress.processed || 0} / ${backfillProgress.total || 0}`"
    >
      <template #action>
        <a-progress
          :percent="backfillPercent"
          size="small"
          :status="backfillProgress.status === 'done' ? 'success' : 'active'"
        />
      </template>
    </a-alert>

    <a-empty v-if="!loading && data.length === 0" description="没有符合筛选条件的会话" />

    <a-list
      v-else
      class="lead-list"
      :loading="loading"
      :data-source="data"
      bordered
    >
      <template #renderItem="{ item }">
        <a-list-item class="lead-list-item">
          <div class="lead-row">
            <a-checkbox
              :checked="selectedIds.includes(item.id)"
              class="lead-check"
              @change="(e) => toggleSelect(item.id, e.target.checked)"
              @click.stop
            />
            <button
              type="button"
              class="lead-row-button"
              :aria-label="`查看 ${item.kol_name || '联系人'} 的会话详情`"
              @click="$router.push(`/threads/${item.id}`)"
            >
              <div class="lead-main">
                <div class="lead-name">{{ item.kol_name || '未命名联系人' }}</div>
                <div class="lead-meta">
                  <a-tag v-if="item.campaign_name || item.campaign_id" color="geekblue">
                    {{ campaignLabel(item) }}
                  </a-tag>
                  <span>{{ item.reply_count || 0 }} 封回信</span>
                  <span v-if="item.assignee_name">负责人：{{ item.assignee_name }}</span>
                  <span v-else>未分配</span>
                </div>
              </div>

              <div class="lead-state">
                <a-tag :color="intentColor(item.last_intent)">
                  {{ intentLabel(item.last_intent) }} · {{ item.intent_score }}
                </a-tag>
                <RightOutlined class="lead-arrow" />
              </div>
            </button>
          </div>
        </a-list-item>
      </template>
    </a-list>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { message as antMessage } from 'ant-design-vue'
import { ReloadOutlined, RightOutlined, ThunderboltOutlined, DownloadOutlined } from '@ant-design/icons-vue'
import { snovApi, threadApi } from '../api'

const data = ref([])
const campaigns = ref([])
const loading = ref(false)
const filter = ref('hot')
const campaignId = ref(undefined)
let refreshTimer = null

// ===== 勾选 =====
const selectedIds = ref([])

function toggleSelect(id, checked) {
  if (checked) {
    if (!selectedIds.value.includes(id)) selectedIds.value.push(id)
  } else {
    selectedIds.value = selectedIds.value.filter((x) => x !== id)
  }
}

const allSelected = computed(() => data.value.length > 0 && selectedIds.value.length === data.value.length)
const someSelected = computed(() => selectedIds.value.length > 0)

function toggleSelectAll(e) {
  const checked = e.target?.checked ?? !allSelected.value
  selectedIds.value = checked ? data.value.map((d) => d.id) : []
}

// ===== AI 补全画像 =====
const backfilling = ref(false)
const backfillProgress = ref({})
let statusTimer = null

async function startBackfill() {
  if (backfilling.value) return
  backfilling.value = true
  backfillProgress.value = { processed: 0, total: 0, status: 'running' }
  try {
    // 不传 thread_ids → 后端处理全部 hot；勾选了就只处理勾选的
    const ids = selectedIds.value.length > 0 ? selectedIds.value : null
    const resp = await threadApi.backfillProfile(ids, false)
    if (!resp.job_id) {
      antMessage.info(resp.message || '没有需要处理的会话')
      backfilling.value = false
      return
    }
    antMessage.success(`已启动 AI 补全（${resp.total} 条），请稍候`)
    // 轮询进度
    statusTimer = window.setInterval(pollBackfillStatus, 2000)
  } catch (e) {
    const detail = e.response?.data?.detail || e.message
    antMessage.error(`启动失败：${detail}`)
    backfilling.value = false
  }
}

async function pollBackfillStatus() {
  try {
    const st = await threadApi.backfillStatus()
    backfillProgress.value = st
    if (st.status === 'done') {
      window.clearInterval(statusTimer)
      statusTimer = null
      backfilling.value = false
      const r = st.result || {}
      antMessage.success(`补全完成：更新 ${r.kol_updated} 个画像，写入 ${r.msg_updated} 条报价明细`)
      await load()  // 刷新看板
    } else if (st.status === 'failed') {
      window.clearInterval(statusTimer)
      statusTimer = null
      backfilling.value = false
      antMessage.error(`补全失败：${st.error}`)
    }
  } catch {
    // 轮询失败静默，下次再试
  }
}

const backfillPercent = computed(() => {
  const { processed = 0, total = 0 } = backfillProgress.value
  return total > 0 ? Math.round((processed / total) * 100) : 0
})

// ===== 导出 =====
const exporting = ref(false)

async function exportSelected() {
  if (selectedIds.value.length === 0) return
  exporting.value = true
  try {
    const blob = await threadApi.exportQuotes(selectedIds.value, 'dola')
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `已报价KOL_${selectedIds.value.length}位_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}.xlsx`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
    antMessage.success(`已导出 ${selectedIds.value.length} 条`)
  } catch (e) {
    const detail = e.response?.data?.detail || e.message
    antMessage.error(`导出失败：${detail}`)
  } finally {
    exporting.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const params = { page: 1, size: 200 }
    if (filter.value === 'unassigned') params.unassigned_only = true
    else if (filter.value) params.status = filter.value
    if (campaignId.value) params.campaign_id = campaignId.value
    data.value = await threadApi.list(params)
    // 筛选切换后，清掉已不在当前列表的选中项
    const ids = new Set(data.value.map((d) => d.id))
    selectedIds.value = selectedIds.value.filter((id) => ids.has(id))
  } finally {
    loading.value = false
  }
}

function campaignLabel(item) {
  return item.campaign_name || `活动 #${item.campaign_id}`
}

function intentColor(intent) {
  return { high: 'red', medium: 'orange', low: 'blue', negative: 'default', ooo: 'purple', auto: 'default' }[intent] || 'default'
}

function intentLabel(intent) {
  return { high: '高意向', medium: '中等', low: '低', negative: '拒绝', ooo: '不在', auto: '自动回' }[intent] || '未知'
}

onMounted(async () => {
  try {
    campaigns.value = await snovApi.campaigns()
  } catch {
    campaigns.value = []
  } finally {
    await load()
    refreshTimer = window.setInterval(load, 120000)
  }
})

onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
  if (statusTimer) window.clearInterval(statusTimer)
})
</script>

<style scoped>
.lead-list-page {
  max-width: 1200px;
}

.lead-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.lead-actions {
  flex: 0 0 auto;
}

.backfill-progress {
  margin-bottom: 16px;
}

.campaign-filter {
  min-width: 230px;
}

.lead-count {
  flex: 0 0 auto;
  color: #64748b;
  font-size: 13px;
}

.lead-list {
  overflow: hidden;
  background: #fff;
  border-color: #e4ecfc;
  border-radius: 10px;
}

.lead-list-item {
  display: block;
  padding: 0;
}

.lead-row {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 72px;
  padding: 12px 18px 12px 12px;
  background: #fff;
  transition: background-color 180ms ease;
}

.lead-row:hover {
  background: #f8fbff;
}

.lead-check {
  flex: 0 0 auto;
  margin-right: 10px;
}

.lead-row-button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 1 1 auto;
  min-width: 0;
  padding: 0;
  color: inherit;
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: 0;
}

.lead-row-button:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: -2px;
}

.lead-main {
  min-width: 0;
}

.lead-name {
  overflow: hidden;
  color: #0f172a;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lead-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin-top: 6px;
  color: #64748b;
  font-size: 13px;
}

.lead-state {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  gap: 14px;
  margin-left: 20px;
}

.lead-arrow {
  color: #94a3b8;
  font-size: 14px;
}

@media (max-width: 640px) {
  .lead-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .campaign-filter {
    width: 100%;
  }

  .lead-row {
    align-items: flex-start;
    min-height: 86px;
    padding: 12px;
  }

  .lead-state {
    gap: 8px;
    margin-left: 12px;
  }

  .lead-state :deep(.ant-tag) {
    display: none;
  }
}
</style>

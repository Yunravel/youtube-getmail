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
      <span class="lead-count">{{ data.length }} 条会话</span>
    </div>

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
          <button
            type="button"
            class="lead-row"
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
        </a-list-item>
      </template>
    </a-list>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ReloadOutlined, RightOutlined } from '@ant-design/icons-vue'
import { snovApi, threadApi } from '../api'

const data = ref([])
const campaigns = ref([])
const loading = ref(false)
const filter = ref('hot')
const campaignId = ref(undefined)
let refreshTimer = null

async function load() {
  loading.value = true
  try {
    const params = { page: 1, size: 200 }
    if (filter.value === 'unassigned') params.unassigned_only = true
    else if (filter.value) params.status = filter.value
    if (campaignId.value) params.campaign_id = campaignId.value
    data.value = await threadApi.list(params)
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
  justify-content: space-between;
  width: 100%;
  min-height: 72px;
  padding: 12px 18px;
  color: inherit;
  text-align: left;
  cursor: pointer;
  background: #fff;
  border: 0;
  transition: background-color 180ms ease;
}

.lead-row:hover {
  background: #f8fbff;
}

.lead-row:focus-visible {
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

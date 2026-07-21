<template>
  <div>
    <!-- 营销效果 KPI：打开率 / 回复率 / 合作率 -->
    <a-card style="margin-bottom: 24px">
      <template #title>
        <a-space align="center" :size="12">
          <span>营销效果</span>
          <a-select
            v-model:value="selectedProject"
            size="small"
            style="width: 140px"
            :loading="projectsLoading"
            @change="loadRates"
          >
            <a-select-option
              v-for="p in projects"
              :key="p.code"
              :value="p.code"
            >{{ p.label }}</a-select-option>
          </a-select>
        </a-space>
      </template>
      <a-row :gutter="24">
        <a-col :span="8" v-for="metric in rateMetrics" :key="metric.key">
          <div class="rate-card">
            <a-progress
              type="dashboard"
              :percent="metric.value"
              :stroke-color="metric.color"
              :size="140"
            />
            <div class="rate-meta">
              <div class="rate-title">{{ metric.title }}</div>
              <div class="rate-sub">{{ metric.sub }}</div>
            </div>
          </div>
        </a-col>
      </a-row>
    </a-card>

    <a-row :gutter="16" style="margin-bottom: 24px">
      <a-col :span="6" v-for="card in cards" :key="card.title">
        <a-card>
          <a-statistic :title="card.title" :value="card.value" :value-style="{ color: card.color }">
            <template #prefix><component :is="card.icon" /></template>
          </a-statistic>
        </a-card>
      </a-col>
    </a-row>

    <a-card title="快速操作">
      <a-space size="large">
        <a-button type="primary" @click="$router.push('/hot-leads')">
          <fire-outlined /> 查看 Hot Lead
        </a-button>
        <a-button @click="$router.push('/kol-import')">
          <upload-outlined /> 导入 KOL
        </a-button>
        <a-button @click="$router.push('/stats')">
          <bar-chart-outlined /> 查看统计
        </a-button>
      </a-space>
    </a-card>

    <a-alert
      v-if="cards[0].value === 0"
      style="margin-top: 24px"
      type="info"
      show-icon
      message="还没有数据"
      description="创建并启动营销活动后，中台会在邮件发出或收到回信时自动同步联系人和会话。"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import {
  TeamOutlined, MessageOutlined, FireOutlined, InboxOutlined,
  UploadOutlined, BarChartOutlined,
} from '@ant-design/icons-vue'
import { statsApi } from '../api'

const data = ref({ total_kols: 0, total_threads: 0, hot_threads: 0, open_threads: 0 })
const rates = ref({
  open_rate: 0,
  reply_rate: 0,
  cooperation_rate: 0,
  open_count: 0,
  reply_count: 0,
  cooperated_count: 0,
  contacted_by_email: 0,
})

// 项目筛选器：all / dola / pippit / ...（动态从后端拉取）
const projects = ref([{ code: 'all', label: '全部项目' }])
const selectedProject = ref('all')
const projectsLoading = ref(false)

const cards = computed(() => [
  { title: 'KOL 总数', value: data.value.total_kols, color: '#1677ff', icon: TeamOutlined },
  { title: '会话总数', value: data.value.total_threads, color: '#722ed1', icon: MessageOutlined },
  { title: 'Hot Lead', value: data.value.hot_threads, color: '#fa541c', icon: FireOutlined },
  { title: '待处理', value: data.value.open_threads, color: '#faad14', icon: InboxOutlined },
])

// dashboard 进度条 percent 上限为 100，且需为整数避免警告。
const clampPercent = (v) => Math.max(0, Math.min(100, Math.round(v || 0)))

const rateMetrics = computed(() => [
  {
    key: 'open',
    title: '打开率',
    value: clampPercent(rates.value.open_rate),
    color: '#1677ff',
    sub: `${rates.value.open_count} 次打开 / ${rates.value.contacted_by_email} 已联系`,
  },
  {
    key: 'reply',
    title: '回复率',
    value: clampPercent(rates.value.reply_rate),
    color: '#52c41a',
    sub: `${rates.value.reply_count} 人回复`,
  },
  {
    key: 'cooperation',
    title: '合作率',
    value: clampPercent(rates.value.cooperation_rate),
    color: '#fa541c',
    sub: `${rates.value.cooperated_count} 意向合作 / ${rates.value.contacted_by_email} 已联系`,
  },
])

async function loadRates() {
  try {
    rates.value = { ...rates.value, ...(await statsApi.campaignRates(selectedProject.value)) }
  } catch (e) {
    /* Snov 未配置或后端旧版，KPI 卡保持 0 */
  }
}

async function loadProjects() {
  projectsLoading.value = true
  try {
    const list = await statsApi.campaignProjects()
    if (Array.isArray(list) && list.length) projects.value = list
  } catch (e) {
    /* 保留默认 全部项目 */
  } finally {
    projectsLoading.value = false
  }
}

async function load() {
  try {
    data.value = await statsApi.overview()
  } catch (e) {
    /* 后端没起就显示 0 */
  }
  await Promise.all([loadProjects(), loadRates()])
}
onMounted(load)
</script>

<style scoped>
.rate-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 12px;
  padding: 8px 0;
}
.rate-meta {
  line-height: 1.4;
}
.rate-title {
  font-size: 16px;
  font-weight: 600;
  color: rgba(0, 0, 0, 0.85);
}
.rate-sub {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.45);
  margin-top: 4px;
}
</style>

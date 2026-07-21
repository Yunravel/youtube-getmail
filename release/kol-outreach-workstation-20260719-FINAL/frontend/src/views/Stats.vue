<template>
  <div>
    <a-row :gutter="16">
      <a-col :span="12">
        <a-card title="总览">
          <a-descriptions :column="1" v-if="overview">
            <a-descriptions-item label="KOL 总数">{{ overview.total_kols }}</a-descriptions-item>
            <a-descriptions-item label="会话总数">{{ overview.total_threads }}</a-descriptions-item>
            <a-descriptions-item label="Hot Lead">{{ overview.hot_threads }}</a-descriptions-item>
            <a-descriptions-item label="待处理">{{ overview.open_threads }}</a-descriptions-item>
          </a-descriptions>
        </a-card>
      </a-col>
      <a-col :span="12">
        <a-card title="意向分布">
          <a-empty v-if="distribution.length === 0" />
          <div v-else>
            <a-statistic
              v-for="d in distribution"
              :key="d.intent"
              :title="intentLabel(d.intent)"
              :value="d.count"
              style="margin-bottom: 16px"
            />
          </div>
        </a-card>
      </a-col>
    </a-row>
    <a-alert
      style="margin-top: 24px"
      type="info"
      show-icon
      message="更详细的统计(响应时长、处理量趋势)将在 Day 12-14 完善"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { statsApi } from '../api'

const overview = ref(null)
const distribution = ref([])

function intentLabel(i) {
  return { high: '高意向', medium: '中等', low: '低', negative: '拒绝', ooo: '不在', auto: '自动回' }[i] || i
}

onMounted(async () => {
  try {
    overview.value = await statsApi.overview()
    distribution.value = await statsApi.intentDistribution()
  } catch (e) {}
})
</script>

<template>
  <div>
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

const cards = computed(() => [
  { title: 'KOL 总数', value: data.value.total_kols, color: '#1677ff', icon: TeamOutlined },
  { title: '会话总数', value: data.value.total_threads, color: '#722ed1', icon: MessageOutlined },
  { title: 'Hot Lead', value: data.value.hot_threads, color: '#fa541c', icon: FireOutlined },
  { title: '待处理', value: data.value.open_threads, color: '#faad14', icon: InboxOutlined },
])

async function load() {
  try {
    data.value = await statsApi.overview()
  } catch (e) {
    /* 后端没起就显示 0 */
  }
}
onMounted(load)
</script>

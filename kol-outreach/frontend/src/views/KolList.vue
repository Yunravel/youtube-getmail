<template>
  <div>
    <a-alert
      type="info"
      show-icon
      style="margin-bottom: 16px"
      message="联系人会在邮件发出或收到回信时自动同步到这里；也可以手动导入 CSV。"
    />
    <a-space style="margin-bottom: 16px">
      <a-select
        v-model:value="filters.status"
        style="width: 150px"
        placeholder="状态"
        allow-clear
        @change="load"
      >
        <a-select-option value="pending">待发</a-select-option>
        <a-select-option value="sent">已发</a-select-option>
        <a-select-option value="in_conversation">对话中</a-select-option>
        <a-select-option value="closed">已结束</a-select-option>
      </a-select>
      <a-input
        v-model:value="filters.niche"
        style="width: 150px"
        placeholder="赛道"
        allow-clear
        @change="load"
      />
      <a-button @click="load"><reload-outlined /> 刷新</a-button>
      <a-button
        type="primary"
        :disabled="selectedIds.length === 0"
        :loading="generating"
        @click="showGenerateModal = true"
      >
        <thunderbolt-outlined /> 生成开场白 ({{ selectedIds.length }})
      </a-button>
      <a-button
        :disabled="selectedIds.length === 0"
        :loading="pushingToSnov"
        @click="openSnovModal"
      >
        <send-outlined /> 创建 Snov 待发送名单 ({{ selectedIds.length }})
      </a-button>
    </a-space>

    <a-table
      :columns="columns"
      :data-source="data"
      :pagination="{ current: page, pageSize: size, total, onChange: onPage }"
      :loading="loading"
      row-key="id"
      size="middle"
      :row-selection="{
        selectedRowKeys: selectedIds,
        onChange: onSelectChange,
        preserveSelectedRowKeys: true,
        getCheckboxProps: selectionProps,
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'name'">
          <a :href="record.channel_url" target="_blank" v-if="record.channel_url">{{ record.name }}</a>
          <span v-else>{{ record.name }}</span>
        </template>
        <template v-else-if="column.key === 'subscribers'">
          {{ formatNum(record.subscribers) }}
        </template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="statusColor(record.status)">{{ statusLabel(record.status) }}</a-tag>
        </template>
        <template v-else-if="column.key === 'personal_intro'">
          <a-tooltip :title="record.personal_intro">
            <span>{{ record.personal_intro ? record.personal_intro.slice(0, 30) + '…' : '—' }}</span>
          </a-tooltip>
        </template>
        <template v-else-if="column.key === 'action'">
          <a-popconfirm title="确定删除?" @confirm="del(record.id)">
            <a style="color: #ff4d4f">删除</a>
          </a-popconfirm>
        </template>
      </template>
    </a-table>

    <a-modal
      v-model:open="showGenerateModal"
      title="批量生成个性化开场白"
      :confirm-loading="generating"
      @ok="doGenerate"
    >
      <a-alert
        type="info"
        show-icon
        style="margin-bottom: 16px"
        message="该功能只生成文案，不会从中台发送邮件；请自行复制到对应的营销活动。"
      />
      <a-form layout="vertical">
        <a-form-item label="产品/合作简述">
          <a-textarea
            v-model:value="ourProduct"
            :rows="3"
            placeholder="例：一款面向创作者的 AI 视频剪辑工具"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="showSnovModal"
      title="创建 Snov 待发送名单"
      :confirm-loading="pushingToSnov"
      ok-text="创建并推送"
      cancel-text="取消"
      @ok="doPushToSnov"
    >
      <a-alert
        type="warning"
        show-icon
        style="margin-bottom: 16px"
        message="系统会新建独立名单，不会创建 Campaign 或直接发送邮件。"
      />
      <a-form layout="vertical">
        <a-form-item label="名单名称" required>
          <a-input
            v-model:value="snovListName"
            :maxlength="200"
            placeholder="例：待发送-20260719-1430"
            @press-enter="doPushToSnov"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="showSnovResultModal"
      title="Snov 名单创建结果"
      :footer="null"
      width="720px"
    >
      <a-descriptions v-if="snovResult" bordered size="small" :column="3">
        <a-descriptions-item label="名单">{{ snovResult.list_name }}</a-descriptions-item>
        <a-descriptions-item label="新增">{{ snovResult.added }}</a-descriptions-item>
        <a-descriptions-item label="更新">{{ snovResult.updated }}</a-descriptions-item>
        <a-descriptions-item label="已存在">{{ snovResult.unchanged }}</a-descriptions-item>
        <a-descriptions-item label="跳过">{{ snovResult.skipped }}</a-descriptions-item>
        <a-descriptions-item label="失败">{{ snovResult.failed }}</a-descriptions-item>
      </a-descriptions>
      <a-alert
        v-if="problemResults.length"
        type="warning"
        show-icon
        style="margin-top: 16px"
        message="以下联系人未成功推送，可修正后再次提交。"
      />
      <a-table
        v-if="problemResults.length"
        style="margin-top: 12px"
        size="small"
        :pagination="false"
        :columns="resultColumns"
        :data-source="problemResults"
        :row-key="(record, index) => `${record.kol_id}-${index}`"
      />
    </a-modal>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { message } from 'ant-design-vue'
import { ReloadOutlined, SendOutlined, ThunderboltOutlined } from '@ant-design/icons-vue'
import { kolApi, snovApi } from '../api'

const columns = [
  { title: 'ID', dataIndex: 'id', width: 60 },
  { title: '博主名', key: 'name' },
  { title: '邮箱', dataIndex: 'email' },
  { title: '粉丝', key: 'subscribers', width: 100 },
  { title: '国家', dataIndex: 'country', width: 80 },
  { title: '赛道', dataIndex: 'niche', width: 100 },
  { title: 'AI 开场白', key: 'personal_intro' },
  { title: '状态', key: 'status', width: 100 },
  { title: '操作', key: 'action', width: 80 },
]
const resultColumns = [
  { title: 'KOL', dataIndex: 'name', width: 160 },
  { title: '邮箱', dataIndex: 'email', width: 220 },
  { title: '结果', dataIndex: 'status_label', width: 90 },
  { title: '原因', dataIndex: 'reason' },
]

const data = ref([])
const loading = ref(false)
const page = ref(1)
const size = ref(50)
const total = ref(0)
const filters = reactive({ status: undefined, niche: undefined })
const selectedIds = ref([])
const showGenerateModal = ref(false)
const generating = ref(false)
const ourProduct = ref('')
const showSnovModal = ref(false)
const showSnovResultModal = ref(false)
const pushingToSnov = ref(false)
const snovListName = ref('')
const snovResult = ref(null)
const problemResults = computed(() =>
  (snovResult.value?.results || [])
    .filter(item => ['failed', 'skipped'].includes(item.status))
    .map(item => ({
      ...item,
      status_label: item.status === 'failed' ? '失败' : '跳过',
    }))
)
let refreshTimer = null

async function load() {
  loading.value = true
  try {
    data.value = await kolApi.list({
      status: filters.status,
      niche: filters.niche,
      page: page.value,
      size: size.value,
    })
    total.value = data.value.length < size.value
      ? (page.value - 1) * size.value + data.value.length
      : page.value * size.value + 1
  } finally {
    loading.value = false
  }
}

function onPage(p) {
  page.value = p
  load()
}

function onSelectChange(keys) {
  selectedIds.value = keys
}

function selectionProps(record) {
  const disabled = record.status !== 'pending' || !record.email
  return {
    disabled,
    title: disabled ? '仅有主邮箱的待发 KOL 可以推送到 Snov' : undefined,
  }
}

function defaultSnovListName() {
  const now = new Date()
  const pad = value => String(value).padStart(2, '0')
  return `待发送-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`
}

function openSnovModal() {
  snovListName.value = defaultSnovListName()
  showSnovModal.value = true
}

async function doPushToSnov() {
  const name = snovListName.value.trim()
  if (!name) {
    message.warning('请输入名单名称')
    return
  }
  if (pushingToSnov.value) return

  pushingToSnov.value = true
  try {
    const result = await snovApi.createProspectListFromKols(name, selectedIds.value)
    snovResult.value = result
    const succeeded = new Set(result.successful_kol_ids || [])
    selectedIds.value = selectedIds.value.filter(id => !succeeded.has(id))
    showSnovModal.value = false
    showSnovResultModal.value = true
    if (!result.list_id) {
      message.warning('没有符合条件的联系人，未创建 Snov 名单')
    } else if (result.failed || result.skipped) {
      message.warning(`名单已创建，成功 ${succeeded.size} 条，跳过 ${result.skipped} 条，失败 ${result.failed} 条`)
    } else {
      message.success(`Snov 名单创建完成，共处理 ${succeeded.size} 位 KOL`)
    }
    load()
  } catch (e) {
    message.error(e.response?.data?.detail || '创建 Snov 待发送名单失败')
  } finally {
    pushingToSnov.value = false
  }
}

async function doGenerate() {
  generating.value = true
  try {
    const r = await kolApi.generateIntros(selectedIds.value, ourProduct.value || 'our product')
    message.success(`生成完成: ${r.generated} 条，跳过 ${r.skipped} 条`)
    showGenerateModal.value = false
    selectedIds.value = []
    load()
  } catch (e) {
    message.error('生成失败')
  } finally {
    generating.value = false
  }
}

async function del(id) {
  await kolApi.delete(id)
  message.success('已删除')
  load()
}

function formatNum(n) {
  if (!n) return '0'
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K'
  return String(n)
}

function statusColor(s) {
  return { pending: 'default', sent: 'blue', in_conversation: 'orange', closed: 'green', blacklisted: 'red' }[s] || 'default'
}
function statusLabel(s) {
  return { pending: '待发', sent: '已发', in_conversation: '对话中', closed: '已结束', blacklisted: '无效' }[s] || s
}

onMounted(async () => {
  await load()
  refreshTimer = window.setInterval(load, 120000)
})

onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
})
</script>

<template>
  <div>
    <a-alert
      type="info"
      show-icon
      style="margin-bottom: 16px"
      message="联系人会在邮件发出或收到回信时自动同步到这里；也可以手动导入 CSV。"
    />
    <a-space style="margin-bottom: 16px" wrap>
      <a-select
        v-model:value="filters.status"
        style="width: 140px"
        placeholder="状态"
        allow-clear
        @change="applyFilter"
      >
        <a-select-option value="pending">待发</a-select-option>
        <a-select-option value="sent">已发</a-select-option>
        <a-select-option value="in_conversation">对话中</a-select-option>
        <a-select-option value="closed">已结束</a-select-option>
      </a-select>
      <a-select
        v-model:value="filters.niche"
        style="width: 160px"
        placeholder="赛道"
        allow-clear
        show-search
        :options="nicheOptions"
        :field-names="{ label: 'value', value: 'value' }"
        :filter-option="(input, option) => option.value.toLowerCase().includes(input.toLowerCase())"
        @change="applyFilter"
      />
      <a-select
        v-model:value="filters.country"
        style="width: 180px"
        placeholder="国家"
        allow-clear
        show-search
        :options="countryOptions"
        :field-names="{ label: 'value', value: 'value' }"
        :filter-option="(input, option) => option.value.toLowerCase().includes(input.toLowerCase())"
        @change="applyFilter"
      />
      <a-input-number
        v-model:value="filters.minFollowers"
        style="width: 130px"
        placeholder="最小粉丝"
        :min="0"
        :step="1000"
        :formatter="value => value ? Number(value).toLocaleString() : ''"
        :parser="value => value.replace(/[^\d]/g, '')"
        @change="applyFilter"
      />
      <a-input-number
        v-model:value="filters.maxFollowers"
        style="width: 130px"
        placeholder="最大粉丝"
        :min="0"
        :step="1000"
        :formatter="value => value ? Number(value).toLocaleString() : ''"
        :parser="value => value.replace(/[^\d]/g, '')"
        @change="applyFilter"
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
        :loading="creatingSendList"
        @click="openSendListModal"
      >
        <send-outlined /> 创建待发送名单 ({{ selectedIds.length }})
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
      v-model:open="showSendListModal"
      title="创建待发送名单"
      :confirm-loading="creatingSendList"
      ok-text="创建并推送"
      cancel-text="取消"
      @ok="createSendList"
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
            v-model:value="sendListName"
            :maxlength="200"
            placeholder="例：待发送-20260719-1430"
            @press-enter="createSendList"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="showSendListResultModal"
      title="待发送名单创建结果"
      :footer="null"
      width="720px"
    >
      <a-descriptions v-if="sendListResult" bordered size="small" :column="3">
        <a-descriptions-item label="名单">{{ sendListResult.list_name }}</a-descriptions-item>
        <a-descriptions-item label="新增">{{ sendListResult.added }}</a-descriptions-item>
        <a-descriptions-item label="更新">{{ sendListResult.updated }}</a-descriptions-item>
        <a-descriptions-item label="已存在">{{ sendListResult.unchanged }}</a-descriptions-item>
        <a-descriptions-item label="跳过">{{ sendListResult.skipped }}</a-descriptions-item>
        <a-descriptions-item label="失败">{{ sendListResult.failed }}</a-descriptions-item>
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
import { campaignApi, kolApi } from '../api'

const columns = [
  { title: 'ID', dataIndex: 'id', width: 60 },
  { title: '博主名', key: 'name' },
  { title: '邮箱', dataIndex: 'email' },
  { title: '粉丝', key: 'subscribers', width: 100 },
  { title: '国家', dataIndex: 'country', width: 100 },
  { title: '赛道', dataIndex: 'niche', width: 120 },
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
const filters = reactive({
  status: undefined,
  niche: undefined,
  country: undefined,
  minFollowers: undefined,
  maxFollowers: undefined,
})
const countryOptions = ref([])
const nicheOptions = ref([])
const selectedIds = ref([])
const showGenerateModal = ref(false)
const generating = ref(false)
const ourProduct = ref('')
const showSendListModal = ref(false)
const showSendListResultModal = ref(false)
const creatingSendList = ref(false)
const sendListName = ref('')
const sendListResult = ref(null)
const problemResults = computed(() =>
  (sendListResult.value?.results || [])
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
    const res = await kolApi.list({
      status: filters.status,
      niche: filters.niche,
      country: filters.country,
      min_followers: filters.minFollowers,
      max_followers: filters.maxFollowers,
      page: page.value,
      size: size.value,
    })
    // 后端返回 {items, total}；兼容旧版裸 list 返回。
    data.value = res.items ?? res ?? []
    total.value = res.total ?? data.value.length
  } finally {
    loading.value = false
  }
}

async function loadFilterOptions() {
  try {
    const res = await kolApi.filterOptions()
    countryOptions.value = (res.countries || []).map(c => ({ value: c, label: c }))
    nicheOptions.value = (res.niches || []).map(n => ({ value: n, label: n }))
  } catch (e) {
    // 筛选选项加载失败不阻塞列表展示
    countryOptions.value = []
    nicheOptions.value = []
  }
}

function onPage(p) {
  page.value = p
  load()
}

// 任意筛选条件变化时回到第 1 页再查询，避免在非首页筛选导致空页。
function applyFilter() {
  page.value = 1
  load()
}

function onSelectChange(keys) {
  selectedIds.value = keys
}

function selectionProps(record) {
  const disabled = record.status !== 'pending' || !record.email
  return {
    disabled,
    title: disabled ? '仅有主邮箱的待发 KOL 可以加入待发送名单' : undefined,
  }
}

function defaultSendListName() {
  const now = new Date()
  const pad = value => String(value).padStart(2, '0')
  return `待发送-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`
}

function openSendListModal() {
  sendListName.value = defaultSendListName()
  showSendListModal.value = true
}

async function createSendList() {
  const name = sendListName.value.trim()
  if (!name) {
    message.warning('请输入名单名称')
    return
  }
  if (creatingSendList.value) return

  creatingSendList.value = true
  try {
    const result = await campaignApi.createProspectListFromKols(name, selectedIds.value)
    sendListResult.value = result
    const succeeded = new Set(result.successful_kol_ids || [])
    selectedIds.value = selectedIds.value.filter(id => !succeeded.has(id))
    showSendListModal.value = false
    showSendListResultModal.value = true
    if (!result.list_id) {
      message.warning('没有符合条件的联系人，未创建待发送名单')
    } else if (result.failed || result.skipped) {
      message.warning(`名单已创建，成功 ${succeeded.size} 条，跳过 ${result.skipped} 条，失败 ${result.failed} 条`)
    } else {
      message.success(`待发送名单创建完成，共处理 ${succeeded.size} 位 KOL`)
    }
    load()
  } catch (e) {
    message.error(e.response?.data?.detail || '创建待发送名单失败')
  } finally {
    creatingSendList.value = false
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
  loadFilterOptions()
  await load()
  refreshTimer = window.setInterval(load, 120000)
})

onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
})
</script>

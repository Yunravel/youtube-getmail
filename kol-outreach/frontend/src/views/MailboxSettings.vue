<template>
  <div class="mailbox-settings-page">
    <a-typography-title :level="4" style="margin-top: 0">邮箱配置（IMAP 附件同步）</a-typography-title>
    <a-typography-paragraph type="secondary">
      发信平台不传附件数据。这里直连每个发信邮箱的 Gmail，把 KOL 回信里的真附件抓回中台本地存储。
      先点"批量导入邮箱"一键拉取已配置的发信邮箱地址，再逐个填写 Gmail 应用专用密码。
      <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener">→ 生成应用专用密码</a>
    </a-typography-paragraph>

    <a-card style="margin-bottom: 16px">
      <a-space>
        <a-button type="primary" :loading="importing" @click="importFromProvider">
          <template #icon><download-outlined /></template>
          批量导入邮箱
        </a-button>
        <a-button :loading="loading" @click="load">
          <template #icon><reload-outlined /></template>
          刷新
        </a-button>
        <a-tag v-if="encryptionEnabled" color="green">密码加密存储 ✓</a-tag>
        <a-tag v-else color="orange">密码未加密（请配 ATTACHMENT_MASTER_KEY）</a-tag>
        <a-statistic title="邮箱总数" :value="credentials.length" style="margin-left: 16px" />
        <a-statistic title="已配密码" :value="withPasswordCount" />
      </a-space>
    </a-card>

    <a-card title="发信邮箱列表" :bordered="false">
      <a-table
        :data-source="credentials"
        :columns="columns"
        :loading="loading"
        :pagination="{ pageSize: 50, showSizeChanger: false }"
        row-key="id"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'password'">
            <a-tag :color="record.has_password ? 'green' : 'orange'">
              {{ record.has_password ? '已配置' : '未配置' }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'enabled'">
            <a-switch
              :checked="record.enabled"
              size="small"
              @change="val => toggleEnabled(record, val)"
            />
          </template>
          <template v-else-if="column.key === 'smtp_status'">
            <a-tooltip v-if="record.smtp_last_error" :title="record.smtp_last_error">
              <a-tag color="red">测试失败</a-tag>
            </a-tooltip>
            <a-tag v-else-if="record.smtp_verified_at" color="green">已验证</a-tag>
            <a-tag v-else color="orange">未测试</a-tag>
          </template>
          <template v-else-if="column.key === 'sync_status'">
            <a-tooltip v-if="record.last_error" :title="record.last_error">
              <a-tag color="red">{{ record.last_sync_status || '失败' }}</a-tag>
            </a-tooltip>
            <a-tag v-else-if="record.last_sync_status === 'success'" color="green">成功</a-tag>
            <a-tag v-else-if="record.last_sync_status === 'partial'" color="orange">部分</a-tag>
            <a-tag v-else-if="record.last_synced_at" color="blue">{{ record.last_sync_status }}</a-tag>
            <span v-else style="color: #ccc">未同步</span>
            <div v-if="record.last_synced_at" style="font-size: 11px; color: #999">
              {{ formatTime(record.last_synced_at) }}
            </div>
          </template>
          <template v-else-if="column.key === 'action'">
            <a-button type="link" size="small" @click="openModal(record)">编辑</a-button>
            <a-button type="link" size="small" :loading="testingSmtpId === record.id" @click="testSmtp(record)">测试 SMTP</a-button>
            <a-popconfirm
              title="确定删除这个邮箱凭据吗？"
              ok-text="删除"
              cancel-text="取消"
              @confirm="removeCred(record)"
            >
              <a-button type="link" danger size="small">删除</a-button>
            </a-popconfirm>
          </template>
        </template>
      </a-table>
    </a-card>

    <!-- 同步触发卡片 -->
    <a-card title="手动同步附件" style="margin-top: 16px" :bordered="false">
      <a-space align="end" wrap>
        <a-form-item label="时间范围" style="margin: 0">
          <a-select v-model:value="syncSinceDays" style="width: 140px">
            <a-select-option :value="7">最近 7 天</a-select-option>
            <a-select-option :value="30">最近 30 天</a-select-option>
            <a-select-option :value="90">最近 90 天</a-select-option>
            <a-select-option :value="0">全部历史</a-select-option>
          </a-select>
        </a-form-item>
        <a-button type="primary" :loading="syncing" :disabled="syncJob.status === 'running'" @click="triggerSync">
          <template #icon><sync-outlined /></template>
          开始同步
        </a-button>
      </a-space>
      <div v-if="syncJob.status" style="margin-top: 16px">
        <a-alert
          :type="syncAlertType"
          :message="syncMessage"
          show-icon
        />
        <div v-if="syncJob.status === 'running'" style="margin-top: 8px">
          <a-progress
            :percent="syncJob.total ? Math.round(syncJob.processed / syncJob.total * 100) : 0"
            :status="syncJob.status === 'running' ? 'active' : 'normal'"
          />
          <div style="font-size: 12px; color: #999; margin-top: 4px">
            {{ syncJob.processed }} / {{ syncJob.total }} 邮箱
            <span v-if="syncJob.current_email">· 当前：{{ syncJob.current_email }}</span>
          </div>
        </div>
        <div v-if="syncJob.status === 'done' && syncJob.result" style="margin-top: 8px">
          <a-descriptions size="small" :column="5" bordered>
            <a-descriptions-item label="邮箱">{{ syncJob.result.mailboxes_ok }}/{{ syncJob.result.mailboxes_total }} 成功</a-descriptions-item>
            <a-descriptions-item label="抓取邮件">{{ syncJob.result.fetched }}</a-descriptions-item>
            <a-descriptions-item label="匹配会话">{{ syncJob.result.matched }}</a-descriptions-item>
            <a-descriptions-item label="下载附件">{{ syncJob.result.attached }}</a-descriptions-item>
            <a-descriptions-item label="修复收件邮箱">{{ syncJob.result.repaired_recipients || 0 }}</a-descriptions-item>
          </a-descriptions>
        </div>
      </div>
      <div style="color: #999; font-size: 12px; margin-top: 8px">
        定时轮询（每 10 分钟）会扫描所有启用邮箱近两天的邮件，不会因邮件已读而漏掉；这里手动触发用于补抓历史或重试。
      </div>
    </a-card>

    <!-- 新增/编辑弹窗 -->
    <a-modal
      v-model:open="showModal"
      :title="editingCred ? '编辑邮箱凭据' : '新增邮箱凭据'"
      :confirm-loading="saving"
      ok-text="保存"
      cancel-text="取消"
      @ok="saveCred"
    >
      <a-form layout="vertical">
        <a-form-item label="邮箱地址" required>
          <a-input
            v-model:value="form.email"
            :disabled="saving || !!editingCred"
            placeholder="例：ducakanita@gmail.com"
          />
        </a-form-item>
        <a-form-item :label="editingCred ? '应用专用密码（留空不改）' : '应用专用密码'" :required="!editingCred">
          <a-input-password
            v-model:value="form.password"
            :disabled="saving"
            placeholder="16 位应用专用密码（Google 后台生成）"
          />
          <div style="color: #999; font-size: 12px; margin-top: 4px">
            Google 账号需先开两步验证，到
            <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener">应用专用密码页</a>
            生成。
          </div>
        </a-form-item>
        <a-form-item label="IMAP 服务器">
          <a-input v-model:value="form.imap_host" :disabled="saving" />
          <div style="color: #999; font-size: 12px">Gmail 固定 imap.gmail.com，端口 993，一般无需改。</div>
        </a-form-item>
        <a-row :gutter="12">
          <a-col :span="14">
            <a-form-item label="SMTP 服务器">
              <a-input v-model:value="form.smtp_host" :disabled="saving" />
            </a-form-item>
          </a-col>
          <a-col :span="6">
            <a-form-item label="端口">
              <a-input-number v-model:value="form.smtp_port" :min="1" :max="65535" style="width: 100%" />
            </a-form-item>
          </a-col>
          <a-col :span="4">
            <a-form-item label="SSL">
              <a-switch v-model:checked="form.smtp_use_ssl" />
            </a-form-item>
          </a-col>
        </a-row>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, onUnmounted } from 'vue'
import { message as antMessage } from 'ant-design-vue'
import {
  DownloadOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons-vue'
import dayjs from 'dayjs'
import { mailboxCredentialApi, attachmentApi } from '../api'

const credentials = ref([])
const loading = ref(false)
const encryptionEnabled = ref(false)
const importing = ref(false)
const testingSmtpId = ref(null)

const withPasswordCount = computed(() => credentials.value.filter(c => c.has_password).length)

const columns = [
  { title: '邮箱', dataIndex: 'email', key: 'email', ellipsis: true },
  { title: '密码', key: 'password', width: 90 },
  { title: 'SMTP', key: 'smtp_status', width: 100 },
  { title: '同步状态', key: 'sync_status', width: 130 },
  { title: '启用', key: 'enabled', width: 70 },
  { title: '操作', key: 'action', width: 220 },
]

async function load() {
  loading.value = true
  try {
    const [list, status] = await Promise.all([
      mailboxCredentialApi.list(),
      mailboxCredentialApi.status(),
    ])
    credentials.value = list
    encryptionEnabled.value = status.encryption_enabled
  } catch (e) {
    antMessage.error(e.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

async function importFromProvider() {
  importing.value = true
  try {
    const result = await mailboxCredentialApi.importFromProvider()
    antMessage.success(`导入 ${result.imported} 个邮箱（跳过 ${result.skipped_existing} 已存在，${result.skipped_no_imap} 不可同步）`)
    if (result.imported > 0) {
      antMessage.info('导入的邮箱密码为空，请逐个编辑填写应用专用密码', 6)
    }
    await load()
  } catch (e) {
    antMessage.error(e.response?.data?.detail || '导入失败')
  } finally {
    importing.value = false
  }
}

// 新增/编辑弹窗
const showModal = ref(false)
const editingCred = ref(null)
const saving = ref(false)
const form = ref({
  email: '', password: '', imap_host: 'imap.gmail.com',
  smtp_host: 'smtp.gmail.com', smtp_port: 465, smtp_use_ssl: true,
})

function openModal(cred = null) {
  editingCred.value = cred
  form.value = {
    email: cred ? cred.email : '',
    password: '',
    imap_host: cred ? cred.imap_host : 'imap.gmail.com',
    smtp_host: cred ? cred.smtp_host : 'smtp.gmail.com',
    smtp_port: cred ? cred.smtp_port : 465,
    smtp_use_ssl: cred ? cred.smtp_use_ssl : true,
  }
  showModal.value = true
}

async function saveCred() {
  if (!form.value.email) {
    antMessage.warning('请填邮箱地址')
    return
  }
  if (!editingCred.value && !form.value.password) {
    antMessage.warning('请填应用专用密码')
    return
  }
  saving.value = true
  try {
    if (editingCred.value) {
      const data = {
        imap_host: form.value.imap_host,
        smtp_host: form.value.smtp_host,
        smtp_port: form.value.smtp_port,
        smtp_use_ssl: form.value.smtp_use_ssl,
      }
      if (form.value.password) data.password = form.value.password
      await mailboxCredentialApi.update(editingCred.value.id, data)
      antMessage.success('已更新')
    } else {
      await mailboxCredentialApi.create({
        email: form.value.email,
        password: form.value.password,
        imap_host: form.value.imap_host,
        smtp_host: form.value.smtp_host,
        smtp_port: form.value.smtp_port,
        smtp_use_ssl: form.value.smtp_use_ssl,
      })
      antMessage.success('已新增')
    }
    showModal.value = false
    await load()
  } catch (e) {
    antMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function testSmtp(record) {
  testingSmtpId.value = record.id
  try {
    await mailboxCredentialApi.testSmtp(record.id)
    antMessage.success(`${record.email} SMTP 验证成功（未发送邮件）`)
    await load()
  } catch (e) {
    antMessage.error(e.response?.data?.detail || 'SMTP 测试失败')
    await load()
  } finally {
    testingSmtpId.value = null
  }
}

async function toggleEnabled(record, val) {
  try {
    await mailboxCredentialApi.update(record.id, { enabled: val })
    record.enabled = val
  } catch (e) {
    antMessage.error('切换失败')
  }
}

async function removeCred(record) {
  try {
    await mailboxCredentialApi.remove(record.id)
    antMessage.success('已删除')
    await load()
  } catch (e) {
    antMessage.error(e.response?.data?.detail || '删除失败')
  }
}

// 同步触发
const syncSinceDays = ref(30)
const syncing = ref(false)
const syncJob = ref({})
let pollTimer = null

async function triggerSync() {
  syncing.value = true
  try {
    const { job_id } = await attachmentApi.sync(syncSinceDays.value)
    syncJob.value = { status: 'running', job_id, processed: 0, total: 0 }
    startPolling(job_id)
  } catch (e) {
    antMessage.error(e.response?.data?.detail || '触发失败')
    syncing.value = false
  }
}

function startPolling(jobId) {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const status = await attachmentApi.syncStatus(jobId)
      syncJob.value = { ...status, job_id: jobId }
      if (status.status !== 'running') {
        stopPolling()
        syncing.value = false
        if (status.status === 'done' && status.result?.attached > 0) {
          antMessage.success(`同步完成，抓到 ${status.result.attached} 个附件`)
        } else if (status.status === 'done') {
          antMessage.info('同步完成，无新附件')
        } else if (status.status === 'failed') {
          antMessage.error(`同步失败：${status.error}`)
        }
        // 刷新凭据状态（last_synced_at 等）
        load()
      }
    } catch (e) {
      // 轮询失败不打断，下次继续
    }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const syncMessage = computed(() => {
  const s = syncJob.value.status
  if (s === 'running') return `正在同步（${syncJob.value.since_days ?? '全部'}）...`
  if (s === 'done') return '同步完成'
  if (s === 'failed') return `同步失败：${syncJob.value.error}`
  return ''
})

const syncAlertType = computed(() => {
  const s = syncJob.value.status
  if (s === 'done') return 'success'
  if (s === 'failed') return 'error'
  return 'info'
})

function formatTime(t) {
  return t ? dayjs(t).format('MM-DD HH:mm') : ''
}

onMounted(load)
onUnmounted(stopPolling)
</script>

<style scoped>
.mailbox-settings-page {
  max-width: 1100px;
}
</style>

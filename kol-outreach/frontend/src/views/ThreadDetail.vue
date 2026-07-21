<template>
  <div v-if="thread" class="thread-page">
    <a-page-header
      class="thread-header"
      :title="thread.thread.kol_name || thread.thread.kol_email"
      @back="$router.push(backTarget)"
    >
      <template #subTitle>会话详情</template>
      <template #extra>
        <a-space :size="12" wrap>
          <a-tag v-if="thread.thread.campaign_name || thread.thread.campaign_id" color="geekblue">
            营销活动 · {{ campaignLabel(thread.thread) }}
          </a-tag>
          <a-tag class="intent-tag" :color="intentColor(thread.thread.last_intent)">
            {{ intentLabel(thread.thread.last_intent) }} · {{ thread.thread.intent_score }} 分
          </a-tag>
          <a-select
            v-model:value="assigneeId"
            class="assignee-select"
            placeholder="分配负责人"
            @change="doAssign"
          >
            <a-select-option v-for="op in operators" :key="op.id" :value="op.id">
              {{ op.name }}
            </a-select-option>
          </a-select>
        </a-space>
      </template>
    </a-page-header>

    <section class="contact-strip" aria-label="联系人信息">
      <div class="contact-primary">
        <span class="contact-avatar"><UserOutlined /></span>
        <div>
          <div class="contact-label">联系人邮箱</div>
          <div class="contact-email">{{ thread.thread.kol_email || '未提供邮箱' }}</div>
        </div>
        <a-button
          v-if="thread.thread.kol_email"
          class="copy-email"
          size="small"
          @click="copyEmail(thread.thread.kol_email)"
        >
          <template #icon><CopyOutlined /></template>
          复制邮箱
        </a-button>
      </div>
      <div class="contact-secondary">
        <span v-if="thread.thread.campaign_name || thread.thread.campaign_id">
          <TagsOutlined /> 营销活动：{{ campaignLabel(thread.thread) }}
        </span>
        <span><MessageOutlined /> {{ thread.messages.length }} 封同步邮件</span>
        <span class="sync-status"><CheckCircleOutlined /> 实时同步已开启</span>
      </div>
    </section>

    <a-row :gutter="[16, 16]">
      <a-col :xs="24" :xl="16">
        <a-card class="conversation-card" :bordered="false">
          <template #title>
            <div class="card-heading">
              <span>邮件会话</span>
              <span class="card-hint">按时间顺序</span>
            </div>
          </template>

          <a-empty
            v-if="thread.messages.length === 0"
            description="暂时还没有同步到邮件"
          />

          <div v-else class="message-list">
            <article
              v-for="msg in thread.messages"
              :key="msg.id"
              class="message-item"
              :class="`message-${msg.direction}`"
            >
              <div class="message-rail">
                <InboxOutlined v-if="msg.direction === 'inbound'" />
                <SendOutlined v-else />
              </div>

              <div class="message-card">
                <div class="message-topline">
                  <div class="message-title-wrap">
                    <span class="message-direction">
                      {{ msg.direction === 'inbound' ? '对方回信' : '我方发出' }}
                    </span>
                    <span class="message-subject">{{ msg.subject || '无主题' }}</span>
                  </div>
                  <time class="message-time">{{ fmtTime(msg.received_at) }}</time>
                </div>

                <div class="message-addresses">
                  <span v-if="msg.direction === 'inbound'">
                    <MailOutlined /> 发件人：<b>{{ msg.from_email }}</b>
                  </span>
                  <span v-else>
                    <MailOutlined /> 收件人：<b>{{ msg.to_email }}</b>
                  </span>
                  <span
                    v-if="msg.direction === 'inbound' && knownAddress(msg.to_email)"
                    class="recipient-address"
                  >
                    收件人：{{ msg.to_email }}
                  </span>
                </div>

                <div class="message-body">{{ msg.body_text || '（邮件未返回正文）' }}</div>

                <div v-if="msg.attachments?.length" class="attachment-list">
                  <div class="attachment-heading">附件</div>
                  <a
                    v-for="(attachment, index) in msg.attachments"
                    :key="attachment.id || attachment.url || attachment.local_path || `${msg.id}-${index}`"
                    class="attachment-item"
                    :href="attachmentHref(msg.id, attachment) || undefined"
                    :target="attachment.url ? '_blank' : undefined"
                    :rel="attachment.url ? 'noopener noreferrer' : undefined"
                    :download="attachment.local_path ? attachment.name : undefined"
                    @click="!attachment.url && !attachment.local_path && $event.preventDefault()"
                  >
                    <PaperClipOutlined />
                    <span>{{ attachment.name || '附件' }}</span>
                    <small v-if="attachment.size">{{ formatBytes(attachment.size) }}</small>
                    <small v-if="attachment.source === 'imap'" class="attach-source">本地</small>
                    <small v-if="!attachment.url && !attachment.local_path">无法下载</small>
                  </a>
                </div>

                <div v-if="msg.ai_analysis" class="message-insight">
                  <RobotOutlined />
                  <div class="message-insight-content">
                    <div><b>意向判断：</b>{{ msg.ai_analysis.summary || '已完成分析' }}</div>
                    <div v-if="formatBudget(msg.ai_analysis.budget_mentioned)" class="message-quote">
                      <TagsOutlined />
                      <b>报价：</b>{{ formatBudget(msg.ai_analysis.budget_mentioned) }}
                    </div>
                  </div>
                </div>
              </div>
            </article>
          </div>
        </a-card>

        <a-alert
          class="manual-reply-alert"
          type="info"
          show-icon
          message="请在发信系统中人工回复"
          description="这里用于查看联系人、邮箱和完整回信；实际回复仍在发信系统内完成。"
        />
      </a-col>

      <a-col :xs="24" :xl="8">
        <a-card class="side-card" title="联系人与意向" :bordered="false">
          <a-descriptions :column="1" size="small">
            <a-descriptions-item label="联系人">
              {{ thread.thread.kol_name || '—' }}
            </a-descriptions-item>
            <a-descriptions-item label="邮箱">
              <span class="side-email">{{ thread.thread.kol_email || '—' }}</span>
            </a-descriptions-item>
            <a-descriptions-item label="任务名（Campaign）">
              {{ campaignLabel(thread.thread) || '—' }}
            </a-descriptions-item>
            <a-descriptions-item label="当前意向">
              <a-tag :color="intentColor(thread.thread.last_intent)">
                {{ intentLabel(thread.thread.last_intent) }}
              </a-tag>
              <span class="score-text">{{ thread.thread.intent_score }}/100</span>
            </a-descriptions-item>
            <a-descriptions-item label="回信数量">
              {{ thread.thread.reply_count }} 封
            </a-descriptions-item>
          </a-descriptions>

          <div class="summary-block">
            <div class="summary-label"><RobotOutlined /> AI 摘要</div>
            <div>{{ thread.thread.ai_summary || '等待回信分析' }}</div>
          </div>

          <div v-if="latestBudget" class="quote-block">
            <div class="quote-label"><TagsOutlined /> 报价</div>
            <div>{{ latestBudget }}</div>
          </div>
        </a-card>

        <a-card class="side-card notes-card" title="内部备注" :bordered="false">
          <a-empty v-if="thread.notes.length === 0" :image-style="{ height: '48px' }" description="还没有备注" />
          <div v-else class="notes-list">
            <div v-for="note in thread.notes" :key="note.id" class="note-item">
              <div class="note-meta">
                <b>{{ note.operator_name || '运营人员' }}</b>
                <span>{{ fmtTime(note.created_at) }}</span>
              </div>
              <p>{{ note.content }}</p>
            </div>
          </div>
          <a-textarea
            v-model:value="noteBody"
            :rows="3"
            placeholder="添加内部备注（不会发给联系人）"
            class="note-input"
          />
          <a-button
            type="primary"
            class="note-submit"
            :disabled="!noteBody || !assigneeId"
            @click="addNote"
          >
            添加备注
          </a-button>
          <div v-if="!assigneeId" class="note-tip">请先在右上角分配负责人，再添加备注。</div>
        </a-card>
      </a-col>
    </a-row>
  </div>
  <a-spin v-else class="page-loading" />
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { message } from 'ant-design-vue'
import {
  CheckCircleOutlined,
  CopyOutlined,
  InboxOutlined,
  MailOutlined,
  MessageOutlined,
  PaperClipOutlined,
  RobotOutlined,
  SendOutlined,
  TagsOutlined,
  UserOutlined,
} from '@ant-design/icons-vue'
import dayjs from 'dayjs'
import { useRoute } from 'vue-router'
import { threadApi, operatorApi } from '../api'

const route = useRoute()
const backTarget = computed(() => route.query.from === 'mailbox' ? '/mailbox' : '/hot-leads')
const threadId = Number(route.params.id)

const thread = ref(null)
const operators = ref([])
const assigneeId = ref(null)
const noteBody = ref('')
let refreshTimer = null

const latestBudget = computed(() => {
  const messages = thread.value?.messages || []
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const msg = messages[index]
    if (msg.direction === 'inbound' && msg.ai_analysis) {
      const budget = formatBudget(msg.ai_analysis.budget_mentioned)
      if (budget) return budget
    }
  }
  return ''
})

async function load() {
  thread.value = await threadApi.detail(threadId)
  assigneeId.value = thread.value.thread.assignee_id
}

async function doAssign() {
  await threadApi.assign(threadId, assigneeId.value)
  message.success('已分配负责人')
}

async function addNote() {
  await threadApi.addNote(threadId, assigneeId.value, noteBody.value)
  noteBody.value = ''
  message.success('备注已添加')
  load()
}

async function copyEmail(email) {
  try {
    await navigator.clipboard.writeText(email)
    message.success('邮箱已复制')
  } catch {
    message.error('复制失败，请手动复制')
  }
}

function knownAddress(email) {
  return email && email !== 'unknown@snov.local'
}

function campaignLabel(source) {
  return source.campaign_name || (source.campaign_id ? `活动 #${source.campaign_id}` : '')
}

function fmtTime(value) {
  return value ? dayjs(value).format('YYYY 年 M 月 D 日 HH:mm') : '时间未知'
}

function formatBytes(value) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes <= 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// 附件下载链接：本地附件走中台下载端点，网盘链接直接外链
function attachmentHref(messageId, attachment) {
  if (attachment.local_path) {
    const filename = attachment.local_path.split('/').pop()
    return `/api/attachments/download/${messageId}/${encodeURIComponent(filename)}`
  }
  return attachment.url || ''
}

function formatBudget(value) {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (Array.isArray(value)) return value.map(formatBudget).filter(Boolean).join('；')
  if (typeof value === 'object') {
    const amount = value.amount ?? value.price
    const currency = value.currency || ''
    const detail = value.deliverable || value.description || ''
    return [currency, amount, detail].filter((item) => item !== '').join(' ')
  }
  return String(value)
}

function intentColor(intent) {
  return { high: 'red', medium: 'orange', low: 'blue', negative: 'default' }[intent] || 'default'
}

function intentLabel(intent) {
  return { high: '高意向', medium: '中等意向', low: '低意向', negative: '拒绝', ooo: '不在办公室', auto: '自动回复' }[intent] || '待判断'
}

onMounted(async () => {
  await Promise.all([load(), operatorApi.list().then((items) => { operators.value = items })])
  refreshTimer = window.setInterval(load, 120000)
})

onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
})
</script>

<style scoped>
.thread-page {
  max-width: 1440px;
  margin: 0 auto;
}

.thread-header {
  padding: 0 0 16px;
}

.intent-tag {
  font-weight: 600;
}

.assignee-select {
  width: 160px;
}

.contact-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 16px 20px;
  margin-bottom: 16px;
  background: #f8fbff;
  border: 1px solid #e4ecfc;
  border-radius: 10px;
}

.contact-primary,
.contact-secondary,
.contact-primary > div {
  display: flex;
  align-items: center;
}

.contact-primary {
  gap: 12px;
  min-width: 0;
}

.contact-primary > div {
  display: block;
  min-width: 0;
}

.contact-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 36px;
  height: 36px;
  color: #2563eb;
  background: #e8f1ff;
  border-radius: 50%;
}

.contact-label,
.card-hint,
.message-time,
.note-meta span,
.note-tip {
  color: #64748b;
  font-size: 12px;
}

.contact-email {
  overflow: hidden;
  color: #0f172a;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.copy-email {
  flex: 0 0 auto;
}

.contact-secondary {
  flex: 0 0 auto;
  gap: 16px;
  color: #475569;
  font-size: 13px;
}

.sync-status {
  color: #15803d;
}

.conversation-card,
.side-card {
  border: 1px solid #e4ecfc;
  border-radius: 10px;
  box-shadow: none;
}

.card-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-width: 280px;
}

.message-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.message-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.message-rail {
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  color: #2563eb;
  background: #e8f1ff;
  border-radius: 50%;
}

.message-outbound .message-rail {
  color: #0369a1;
  background: #e0f2fe;
}

.message-outbound {
  flex-direction: row-reverse;
}

.message-card {
  min-width: 0;
  width: min(84%, 760px);
  padding: 15px 16px;
  background: #fff;
  border: 1px solid #e4ecfc;
  border-radius: 8px;
}

.message-inbound .message-card {
  border-left: 3px solid #2563eb;
}

.message-outbound .message-card {
  background: #f8fcff;
  border-left-color: #e4ecfc;
  border-right: 3px solid #0ea5e9;
}

.message-topline,
.message-title-wrap,
.message-addresses,
.note-meta {
  display: flex;
  align-items: center;
}

.message-topline {
  justify-content: space-between;
  gap: 12px;
}

.message-title-wrap {
  min-width: 0;
  gap: 9px;
}

.message-direction {
  flex: 0 0 auto;
  color: #2563eb;
  font-size: 12px;
  font-weight: 700;
}

.message-outbound .message-direction {
  color: #0369a1;
}

.message-subject {
  overflow: hidden;
  color: #0f172a;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.message-time {
  flex: 0 0 auto;
  white-space: nowrap;
}

.message-addresses {
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 9px;
  color: #475569;
  font-size: 13px;
}

.recipient-address {
  color: #64748b;
}

.message-body {
  margin-top: 14px;
  color: #172033;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.message-insight {
  display: flex;
  gap: 7px;
  padding-top: 11px;
  margin-top: 13px;
  color: #475569;
  font-size: 13px;
  line-height: 1.55;
  border-top: 1px solid #edf2f7;
}

.message-insight-content {
  min-width: 0;
}

.message-quote {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  margin-top: 5px;
  color: #b45309;
  font-weight: 500;
}

.message-insight .message-quote :deep(svg) {
  margin-top: 3px;
  color: #d97706;
}

.attachment-list {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding-top: 11px;
  margin-top: 13px;
  border-top: 1px solid #edf2f7;
}

.attachment-heading {
  color: #475569;
  font-size: 12px;
  font-weight: 600;
}

.attachment-item {
  display: flex;
  align-items: center;
  gap: 7px;
  width: fit-content;
  max-width: 100%;
  color: #2563eb;
  word-break: break-all;
}

.attachment-item small {
  color: #64748b;
  white-space: nowrap;
}

.attachment-item small.attach-source {
  color: #16a34a;
  background: #f0fdf4;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 11px;
}

.message-insight :deep(svg) {
  flex: 0 0 auto;
  margin-top: 3px;
  color: #7c3aed;
}

.manual-reply-alert {
  margin-top: 16px;
  border-radius: 8px;
}

.side-card + .side-card {
  margin-top: 16px;
}

.side-email {
  word-break: break-all;
}

.score-text {
  margin-left: 8px;
  color: #64748b;
}

.summary-block {
  padding: 12px;
  margin-top: 16px;
  color: #334155;
  font-size: 13px;
  line-height: 1.65;
  background: #f8fbff;
  border-radius: 6px;
}

.summary-label {
  margin-bottom: 5px;
  color: #2563eb;
  font-weight: 600;
}

.quote-block {
  padding: 12px;
  margin-top: 10px;
  color: #78350f;
  font-size: 13px;
  line-height: 1.65;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 6px;
}

.quote-label {
  margin-bottom: 5px;
  color: #b45309;
  font-weight: 600;
}

.notes-list {
  margin-bottom: 12px;
}

.note-item {
  padding: 10px 0;
  border-bottom: 1px solid #edf2f7;
}

.note-item:last-child {
  border-bottom: 0;
}

.note-meta {
  justify-content: space-between;
  gap: 12px;
}

.note-item p {
  margin: 5px 0 0;
  color: #334155;
  line-height: 1.55;
  white-space: pre-wrap;
}

.note-input {
  margin-top: 12px;
}

.note-submit {
  margin-top: 8px;
}

.note-tip {
  margin-top: 7px;
}

.page-loading {
  display: block;
  padding: 100px;
  text-align: center;
}

@media (max-width: 767px) {
  .thread-header :deep(.ant-page-header-heading) {
    align-items: flex-start;
  }

  .contact-strip,
  .contact-secondary {
    align-items: flex-start;
    flex-direction: column;
  }

  .contact-secondary {
    gap: 6px;
  }

  .message-item {
    gap: 8px;
  }

  .message-rail {
    width: 28px;
    height: 28px;
    font-size: 13px;
  }

  .message-card {
    width: calc(100% - 36px);
  }

  .message-topline,
  .message-title-wrap {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }

  .message-time {
    margin-top: 2px;
  }
}
</style>

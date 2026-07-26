<template>
  <a-card title="报价自动回复" :bordered="false" style="margin-top: 16px">
    <template #extra>
      <a-space>
        <span>自动发送总开关</span>
        <a-switch :checked="autoEnabled" :loading="toggling" @change="toggleAutomation" />
      </a-space>
    </template>

    <a-alert
      type="warning"
      show-icon
      message="只有明确金额和币种的报价才会排队；启用前必须保存并启用全局模板，且至少一个邮箱通过 SMTP 测试。"
      style="margin-bottom: 16px"
    />

    <a-form layout="vertical">
      <a-row :gutter="16">
        <a-col :xs="24" :md="10">
          <a-form-item label="模板范围">
            <a-select v-model:value="scope" @change="selectScope">
              <a-select-option value="__global__">全局默认模板</a-select-option>
              <a-select-option v-for="campaign in campaigns" :key="campaign.id" :value="`campaign:${campaign.id}`">
                {{ campaign.name }}
              </a-select-option>
            </a-select>
          </a-form-item>
        </a-col>
        <a-col :xs="24" :md="8">
          <a-form-item label="启用此模板">
            <a-switch v-model:checked="form.enabled" />
          </a-form-item>
        </a-col>
      </a-row>
      <a-form-item label="主题模板" required>
        <a-input v-model:value="form.subject_template" />
      </a-form-item>
      <a-form-item label="正文模板" required>
        <a-textarea v-model:value="form.body_template" :rows="10" />
      </a-form-item>
      <a-form-item label="活动背景 / FAQ（AI 只能使用这里的事实回答问题）">
        <a-textarea v-model:value="form.campaign_context" :rows="4" />
      </a-form-item>
      <a-form-item label="额外写作约束">
        <a-textarea v-model:value="form.ai_instructions" :rows="3" />
      </a-form-item>
      <a-form-item label="签名">
        <a-input v-model:value="form.signature" />
      </a-form-item>
      <div class="variable-help">
        可用变量：<code v-pre>{{ creator_name }}</code>、<code v-pre>{{ campaign_name }}</code>、
        <code v-pre>{{ original_subject }}</code>、<code v-pre>{{ quote_summary }}</code>、
        <code v-pre>{{ question_response }}</code>、<code v-pre>{{ sender_name }}</code>、
        <code v-pre>{{ sender_email }}</code>、<code v-pre>{{ signature }}</code>
      </div>
      <a-space style="margin-top: 12px">
        <a-button type="primary" :loading="saving" @click="save">保存模板</a-button>
        <a-button :loading="previewing" @click="preview">预览</a-button>
      </a-space>
    </a-form>

    <a-card v-if="previewData" size="small" title="模板预览" style="margin-top: 16px">
      <b>{{ previewData.subject }}</b>
      <pre class="preview-body">{{ previewData.body }}</pre>
    </a-card>
  </a-card>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { autoReplyApi } from '../api'

const templates = ref([])
const campaigns = ref([])
const scope = ref('__global__')
const autoEnabled = ref(false)
const saving = ref(false)
const previewing = ref(false)
const toggling = ref(false)
const previewData = ref(null)
const defaults = {
  campaign_id: null,
  campaign_name: '全局默认',
  enabled: false,
  subject_template: 'Re: {{ original_subject }}',
  body_template: `Hi {{ creator_name }},

Thank you for sharing your rates. I've noted the following:
{{ quote_summary }}

{{ question_response }}

We'll review this internally and get back to you with next steps.

Best,
{{ signature }}`,
  campaign_context: '',
  ai_instructions: '',
  signature: 'Partnership Team',
}
const form = reactive({ ...defaults })

function applyTemplate(item) {
  Object.assign(form, defaults, item || {})
}

async function load() {
  const result = await autoReplyApi.templates()
  templates.value = result.templates
  campaigns.value = result.campaigns
  const global = templates.value.find(item => item.scope_key === '__global__')
  autoEnabled.value = !!global?.auto_send_enabled
  selectScope(scope.value)
}

function selectScope(value) {
  previewData.value = null
  const existing = templates.value.find(item => item.scope_key === value)
  if (existing) {
    applyTemplate(existing)
    return
  }
  const campaignId = value.startsWith('campaign:') ? value.slice(9) : null
  const campaign = campaigns.value.find(item => item.id === campaignId)
  const global = templates.value.find(item => item.scope_key === '__global__')
  applyTemplate({
    ...(global || defaults),
    campaign_id: campaignId,
    campaign_name: campaign?.name || campaignId || '全局默认',
    enabled: false,
  })
}

function payload() {
  return {
    campaign_id: form.campaign_id,
    campaign_name: form.campaign_name,
    enabled: form.enabled,
    subject_template: form.subject_template,
    body_template: form.body_template,
    campaign_context: form.campaign_context,
    ai_instructions: form.ai_instructions,
    signature: form.signature,
  }
}

async function save() {
  saving.value = true
  try {
    await autoReplyApi.saveTemplate(payload())
    message.success('模板已保存；已排队草稿仍保留原模板快照')
    await load()
  } catch (error) {
    message.error(error.response?.data?.detail || '模板保存失败')
  } finally {
    saving.value = false
  }
}

async function preview() {
  previewing.value = true
  try {
    previewData.value = await autoReplyApi.previewTemplate(payload())
  } catch (error) {
    message.error(error.response?.data?.detail || '预览失败')
  } finally {
    previewing.value = false
  }
}

async function toggleAutomation(enabled) {
  toggling.value = true
  try {
    const result = await autoReplyApi.setEnabled(enabled)
    autoEnabled.value = result.enabled
    message.success(enabled ? '自动发送已开启' : '自动发送已关闭')
  } catch (error) {
    autoEnabled.value = !enabled
    message.error(error.response?.data?.detail || '切换失败')
  } finally {
    toggling.value = false
  }
}

onMounted(() => load().catch(error => message.error(error.response?.data?.detail || '模板加载失败')))
</script>

<style scoped>
.variable-help {
  color: #64748b;
  font-size: 12px;
  word-break: break-word;
}
.preview-body {
  margin: 12px 0 0;
  white-space: pre-wrap;
  font-family: inherit;
}
</style>

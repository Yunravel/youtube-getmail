<template>
  <div class="crawler-page">
    <a-typography-title :level="4" style="margin-top: 0">采集 KOL</a-typography-title>
    <a-typography-paragraph type="secondary">
      按产品关键词发现 YouTube 频道 → 抽取公开邮箱（MX 校验）→ 多平台扩展（IG/TikTok/X）→ 入大数据库与发信池。
      任务在后台运行，可关闭页面；进度下方实时显示。
    </a-typography-paragraph>

    <a-card title="采集配置" style="margin-bottom: 16px">
      <a-form layout="vertical">
        <a-form-item label="产品选择">
          <a-checkbox-group v-model:value="selectedProducts" :disabled="running">
            <a-checkbox
              v-for="p in products"
              :key="p.product"
              :value="p.product"
            >
              {{ p.product }}
              <a-tag color="blue" size="small">{{ p.keyword_count }} 词</a-tag>
            </a-checkbox>
          </a-checkbox-group>
          <div style="color: #999; font-size: 12px; margin-top: 4px">
            可不选产品，只用下方自定义关键词
          </div>
        </a-form-item>

        <a-form-item label="自定义关键词">
          <a-textarea
            v-model:value="customQueriesText"
            :rows="3"
            :disabled="running"
            placeholder="一行一个查询词，可随时补充词表外的关键词扩大覆盖&#10;例：&#10;AI filmmaking tutorial&#10;Blender VFX breakdown&#10;UK productivity vlogger"
          />
          <div style="color: #999; font-size: 12px; margin-top: 4px">
            与产品词表合并查询（{{ customQueries.length }} 个有效词）。产品和自定义可同时用。
          </div>
        </a-form-item>

        <a-form-item label="采集选项">
          <a-space>
            <a-checkbox v-model:checked="enableEnrich" :disabled="running">
              多平台扩展（IG/TikTok/X）
            </a-checkbox>
            <a-checkbox v-model:checked="enableEmail" :disabled="running">
              公开邮箱采集 + MX 校验
            </a-checkbox>
            <a-checkbox v-model:checked="enableDeepEmail" :disabled="running || !enableEmail">
              官网深度邮箱（慢，需 Playwright + 代理）
            </a-checkbox>
          </a-space>
          <div style="color: #999; font-size: 12px; margin-top: 4px">
            深度邮箱：跟进 about 里的官网链接，用浏览器渲染抓 /contact 页邮箱。默认关。
          </div>
        </a-form-item>

        <a-form-item>
          <a-button
            type="primary"
            :loading="running"
            :disabled="selectedProducts.length === 0 && customQueries.length === 0"
            @click="start"
          >
            <template #icon><thunderbolt-outlined /></template>
            开始采集
          </a-button>
          <a-tooltip
            v-if="selectedProducts.length === 0 && customQueries.length === 0"
            title="至少选一个产品，或输入自定义关键词"
          >
            <info-circle-outlined style="margin-left: 8px; color: #999" />
          </a-tooltip>
        </a-form-item>
      </a-form>
    </a-card>

    <a-card v-if="running || progress.status === 'done' || progress.status === 'failed'" title="任务进度" style="margin-bottom: 16px">
      <a-alert
        v-if="running"
        :message="phaseLabel"
        :description="`${progress.processed || 0} / ${progress.total || 0}`"
        type="info"
        show-icon
        style="margin-bottom: 12px"
      />
      <a-progress
        :percent="percent"
        :status="progress.status === 'failed' ? 'exception' : progress.status === 'done' ? 'success' : 'active'"
        style="margin-bottom: 12px"
      />
      <a-descriptions v-if="progress.products" size="small" :column="2" bordered>
        <a-descriptions-item label="产品">{{ progress.products.join(', ') }}</a-descriptions-item>
        <a-descriptions-item label="批次">{{ progress.batch }}</a-descriptions-item>
        <a-descriptions-item label="阶段">{{ phaseLabel }}</a-descriptions-item>
        <a-descriptions-item label="状态">{{ progress.status }}</a-descriptions-item>
      </a-descriptions>
      <a-alert
        v-if="progress.status === 'failed'"
        :message="`采集失败：${progress.error}`"
        type="error"
        show-icon
        style="margin-top: 12px"
      />
    </a-card>

    <a-card v-if="progress.status === 'done' && progress.result" title="采集结果">
      <a-row :gutter="16">
        <a-col :span="6">
          <a-statistic title="发现频道" :value="progress.result.discovered || 0" />
        </a-col>
        <a-col :span="6">
          <a-statistic title="候选行（含扩展）" :value="progress.result.raw_rows || 0" />
        </a-col>
        <a-col :span="6">
          <a-statistic title="有邮箱" :value="progress.result.with_email || 0" />
        </a-col>
        <a-col :span="6">
          <a-statistic title="MX 有效" :value="progress.result.mx_validated || 0" />
        </a-col>
      </a-row>
      <a-divider style="margin: 16px 0" />
      <a-row :gutter="16">
        <a-col :span="8">
          <a-statistic
            title="大数据库新增"
            :value="progress.result.candidate_inserted || 0"
            :value-style="{ color: '#1677ff' }"
          />
          <span v-if="progress.result.candidate_skipped_dup" style="color: #999; font-size: 12px">
            （跳过重复 {{ progress.result.candidate_skipped_dup }}）
          </span>
        </a-col>
        <a-col :span="8">
          <a-statistic
            title="发信池新增"
            :value="progress.result.kol_inserted || 0"
            :value-style="{ color: '#52c41a' }"
          />
          <span v-if="progress.result.kol_skipped_dup" style="color: #999; font-size: 12px">
            （邮箱已存在 {{ progress.result.kol_skipped_dup }}）
          </span>
        </a-col>
        <a-col :span="8">
          <a-statistic
            title="邮箱记录新增"
            :value="progress.result.kol_email_inserted || 0"
            :value-style="{ color: '#722ed1' }"
          />
        </a-col>
      </a-row>
    </a-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { message as antMessage } from 'ant-design-vue'
import {
  ThunderboltOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons-vue'
import { crawlerApi } from '../api'

const products = ref([])
const selectedProducts = ref([])
const customQueriesText = ref('')
const enableEnrich = ref(true)
const enableEmail = ref(true)
const enableDeepEmail = ref(false)

// 把多行文本解析成有效关键词数组（去空白/空行/去重）
const customQueries = computed(() => {
  const seen = new Set()
  const out = []
  for (const line of customQueriesText.value.split('\n')) {
    const q = line.trim()
    if (q && q.length <= 200 && !seen.has(q)) {
      seen.add(q)
      out.push(q)
    }
  }
  return out
})

const running = ref(false)
const progress = ref({})
let statusTimer = null

const phaseMap = {
  queued: '排队中',
  discovery: '发现频道',
  enrich: '抓取 about + 扩展 + 邮箱',
  mx: 'MX 校验',
}
const phaseLabel = computed(() => phaseMap[progress.value.phase] || progress.value.phase || '')

const percent = computed(() => {
  const { processed = 0, total = 0 } = progress.value
  return total > 0 ? Math.round((processed / total) * 100) : 0
})

async function loadProducts() {
  try {
    products.value = await crawlerApi.products()
  } catch (e) {
    antMessage.error('加载产品列表失败')
  }
}

async function start() {
  if (running.value) return
  if (selectedProducts.value.length === 0 && customQueries.value.length === 0) {
    antMessage.warning('至少选一个产品，或输入自定义关键词')
    return
  }
  running.value = true
  progress.value = { status: 'running', phase: 'queued', processed: 0, total: 0 }
  try {
    const resp = await crawlerApi.start(selectedProducts.value, {
      enableEnrich: enableEnrich.value,
      enableEmail: enableEmail.value,
      enableDeepEmail: enableDeepEmail.value,
      customQueries: customQueries.value,
    })
    const desc = selectedProducts.value.length && customQueries.value.length
      ? `${selectedProducts.value.length} 产品 + ${customQueries.value.length} 自定义词`
      : selectedProducts.value.length
        ? `${selectedProducts.value.length} 产品`
        : `${customQueries.value.length} 自定义词`
    antMessage.success(`已启动采集（${desc}），请稍候`)
    statusTimer = window.setInterval(pollStatus, 2000)
  } catch (e) {
    const detail = e.response?.data?.detail || e.message
    antMessage.error(`启动失败：${detail}`)
    running.value = false
  }
}

async function pollStatus() {
  try {
    const st = await crawlerApi.status()
    progress.value = st
    if (st.status === 'done') {
      window.clearInterval(statusTimer)
      statusTimer = null
      running.value = false
      const r = st.result || {}
      antMessage.success(
        `采集完成：发现 ${r.discovered || 0}，入库 candidate +${r.candidate_inserted || 0}，发信池 +${r.kol_inserted || 0}`,
      )
    } else if (st.status === 'failed') {
      window.clearInterval(statusTimer)
      statusTimer = null
      running.value = false
      antMessage.error(`采集失败：${st.error}`)
    }
  } catch {
    // 轮询失败静默，下次再试
  }
}

// 进入页面时若已有任务在跑，恢复轮询
async function checkOngoing() {
  try {
    const st = await crawlerApi.status()
    if (st.status === 'running') {
      running.value = true
      progress.value = st
      statusTimer = window.setInterval(pollStatus, 2000)
    }
  } catch {
    // 忽略
  }
}

onMounted(() => {
  loadProducts()
  checkOngoing()
})

onBeforeUnmount(() => {
  if (statusTimer) window.clearInterval(statusTimer)
})
</script>

<style scoped>
.crawler-page {
  max-width: 960px;
}
</style>

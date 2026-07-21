<template>
  <div>
    <!-- 候选池 Excel 导入（大数据库） -->
    <a-card title="导入 KOL-Find 候选池" size="small" style="margin-bottom: 24px">
      <template #extra>
        <a-tag color="blue">大数据库</a-tag>
      </template>
      <a-alert
        type="info"
        show-icon
        style="margin-bottom: 16px"
        message="Excel 格式说明"
      >
        <template #description>
          <p style="margin: 4px 0">上传 KOL-Find 产出的 <b>.xlsx</b> 文件（须含「全部候选」表，28 列）。</p>
          <p style="margin: 4px 0">处理：<b>全量候选</b>写入大数据库 → <b>有邮箱的</b>自动选入发信池（kol 表）。</p>
          <p style="margin: 4px 0">幂等：重复上传不会产生重复数据，只补新增的行。</p>
        </template>
      </a-alert>

      <a-collapse :bordered="false" style="margin-bottom: 16px">
        <a-collapse-panel key="spec" header="字段格式规范（28 列，点击展开）">
          <a-table
            :data-source="candidateSpec"
            :columns="specColumns"
            :pagination="false"
            size="small"
            :row-key="(r) => r.field"
            bordered
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'required'">
                <a-tag :color="record.required === '必填' ? 'red' : 'default'">{{ record.required }}</a-tag>
              </template>
              <template v-else-if="column.dataIndex === 'field'">
                <code>{{ record.field }}</code>
              </template>
            </template>
          </a-table>
          <p style="margin: 12px 0 0; color: #64748b; font-size: 12px">
            去重键：<code>平台 + 账号</code>（同平台同账号重复导入会被跳过；同人多平台各一行合法）。
            标"源数据空"的列当前无数据，建列保留供未来回填。
          </p>
        </a-collapse-panel>
      </a-collapse>

      <a-upload-dragger
        :before-upload="handleCandidateUpload"
        :show-upload-list="false"
        accept=".xlsx"
        :disabled="candidateLoading"
      >
        <p class="ant-upload-drag-icon"><inbox-outlined /></p>
        <p class="ant-upload-text">{{ candidateLoading ? '导入中…' : '点击或拖拽 Excel 文件到此处' }}</p>
        <p class="ant-upload-hint">仅支持 .xlsx，须含「全部候选」表</p>
      </a-upload-dragger>

      <a-button style="margin-top: 12px" @click="downloadCandidateTemplate">
        <download-outlined /> 下载 Excel 样例模板
      </a-button>

      <a-result
        v-if="candidateResult"
        style="margin-top: 16px"
        :status="candidateResult.candidate_inserted > 0 || candidateResult.kol_inserted > 0 ? 'success' : 'warning'"
        :title="`候选池导入完成`"
      >
        <template #subTitle>
          <div style="font-size: 13px; line-height: 1.8">
            候选库：新增 <b>{{ candidateResult.candidate_inserted }}</b> / 跳过重复 <b>{{ candidateResult.candidate_skipped_dup }}</b>（共 {{ candidateResult.candidate_total }}）<br />
            选入发信池：新增 <b>{{ candidateResult.kol_inserted }}</b> / 邮箱已存在 <b>{{ candidateResult.kol_skipped_dup }}</b>（有邮箱 {{ candidateResult.emailable }}）<br />
            邮箱记录：新增 <b>{{ candidateResult.kol_email_inserted }}</b>
          </div>
        </template>
      </a-result>
    </a-card>

    <!-- 爬虫 CSV 导入（原有） -->
    <a-card title="导入爬虫 CSV" size="small">
      <template #extra>
        <a-tag>爬虫产出</a-tag>
      </template>
      <a-alert
        type="info"
        show-icon
        style="margin-bottom: 16px"
        message="CSV 格式说明"
      >
        <template #description>
          <p style="margin: 4px 0"><b>必填列:</b> name, email</p>
          <p style="margin: 4px 0"><b>可选列:</b> channel_id, channel_url, subscribers, country, niche, recent_videos</p>
          <p style="margin: 4px 0"><b>recent_videos</b> 多个标题用 <code>|</code> 分隔</p>
        </template>
      </a-alert>

      <a-upload-dragger
        :before-upload="handleBeforeUpload"
        :show-upload-list="false"
        accept=".csv"
      >
        <p class="ant-upload-drag-icon"><inbox-outlined /></p>
        <p class="ant-upload-text">点击或拖拽 CSV 文件到此处</p>
        <p class="ant-upload-hint">仅支持单个 .csv 文件,由爬虫产出</p>
      </a-upload-dragger>

      <a-result
        v-if="result"
        style="margin-top: 16px"
        :status="result.imported > 0 ? 'success' : 'warning'"
        :title="`导入完成:成功 ${result.imported} 条,跳过 ${result.skipped} 条`"
      />

      <a-button style="margin-top: 16px" @click="downloadTemplate">
        <download-outlined /> 下载 CSV 模板
      </a-button>
    </a-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { message } from 'ant-design-vue'
import { InboxOutlined, DownloadOutlined } from '@ant-design/icons-vue'
import { kolApi } from '../api'

const result = ref(null)
const candidateResult = ref(null)
const candidateLoading = ref(false)

// 候选池字段规范（基于真实数据实测的非空率与示例）
const specColumns = [
  { title: '#', dataIndex: 'idx', width: 40 },
  { title: 'Excel 列名', dataIndex: 'field', width: 180 },
  { title: '必填', dataIndex: 'required', width: 70 },
  { title: '类型', dataIndex: 'type', width: 90 },
  { title: '说明', dataIndex: 'desc' },
  { title: '示例', dataIndex: 'example', width: 200 },
]
const candidateSpec = [
  { field: '序号', required: '必填', type: '整数', desc: '原表行号，溯源用', example: '1' },
  { field: '平台', required: '必填', type: '枚举', desc: 'YouTube / Instagram / TikTok / X', example: 'Instagram' },
  { field: '账号', required: '必填', type: '文本', desc: '平台账号名（去重键之一）', example: 'fiqri_fox' },
  { field: '主页链接', required: '必填', type: 'URL', desc: '主页/频道完整链接', example: 'https://www.instagram.com/fiqri_fox/' },
  { field: '关联YouTube账号', required: '可选', type: '文本', desc: '识别跨平台同人的软关联键', example: 'CryptoBrosVortex' },
  { field: 'YouTube About来源', required: '可选', type: 'URL', desc: 'YouTube About 页来源', example: 'https://www.youtube.com/@MrPaulXavier/about' },
  { field: '发现方式', required: '可选', type: '文本', desc: '账号发现途径', example: '需求表正向种子' },
  { field: '适配产品', required: '可选', type: '文本', desc: '可含逗号多值', example: 'Kimi, Dola' },
  { field: '主要推荐产品', required: '可选', type: '文本', desc: '单一推荐产品', example: 'Pippit' },
  { field: '命中检索词数', required: '可选', type: '整数', desc: '命中的种子词数量', example: '1' },
  { field: '发现关键词/种子说明', required: '可选', type: '文本', desc: '种子词说明', example: '需求表正向种子：AI影视/叙事' },
  { field: '抓取优先级', required: '可选', type: '枚举', desc: '最高 / 高 / 中 / 低', example: '最高' },
  { field: '邮箱（爬虫回填）', required: '可选', type: '文本', desc: '源数据当前全空', example: '（空）' },
  { field: '邮箱来源URL', required: '可选', type: 'URL', desc: '源数据当前全空', example: '（空）' },
  { field: '邮箱核验状态', required: '可选', type: '文本', desc: '默认"待爬取"', example: '待爬取' },
  { field: '国家/地区', required: '可选', type: '文本', desc: '源数据当前全空', example: '（空）' },
  { field: '语言', required: '可选', type: '文本', desc: '源数据当前全空', example: '（空）' },
  { field: '账号类型', required: '可选', type: '文本', desc: '默认"待核验"', example: '待核验' },
  { field: '粉丝数', required: '可选', type: '整数', desc: '源数据当前全空', example: '（空）' },
  { field: '近期平均播放', required: '可选', type: '整数', desc: '源数据当前全空', example: '（空）' },
  { field: '人工复核状态', required: '可选', type: '文本', desc: '默认"待复核"', example: '待复核' },
  { field: '备注', required: '可选', type: '文本', desc: '源数据当前全空', example: '（空）' },
  { field: '联系邮箱', required: '可选', type: '文本', desc: '有值才选入发信池；可含 | 分隔多邮箱', example: 'collabwithjadhesh@gmail.com' },
  { field: '邮箱状态', required: '可选', type: '枚举', desc: '已获取 / 未获取 / 需登录验证码', example: '已获取' },
  { field: '邮箱来源', required: '可选', type: '文本', desc: '邮箱获取途径', example: '公开页面提取' },
  { field: '其他链接', required: '可选', type: '文本', desc: '多值，| 分隔保留原串', example: 'https://t.co/YBc1Zfp6GN' },
  { field: '采集状态', required: '可选', type: '文本', desc: '成功 / 需要登录验证 / 失败…', example: '成功' },
  { field: '采集时间', required: '可选', type: '时间', desc: 'YYYY-MM-DD HH:MM:SS', example: '2026-07-16 16:37:02' },
].map((r, i) => ({ ...r, idx: i + 1 }))

// 下载候选池 Excel 样例模板（静态文件，放在 public/ 下）
function downloadCandidateTemplate() {
  const a = document.createElement('a')
  a.href = `${import.meta.env.BASE_URL}kol_candidate_template.xlsx`
  a.download = 'KOL候选池导入模板.xlsx'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

// 候选池 Excel 导入
function handleCandidateUpload(file) {
  candidateResult.value = null
  candidateLoading.value = true
  kolApi.importCandidate(file)
    .then((data) => {
      candidateResult.value = data
      const newKol = data.kol_inserted || 0
      const newCand = data.candidate_inserted || 0
      message.success(`候选池导入完成：新增候选 ${newCand}，选入发信池 ${newKol}`)
    })
    .catch((err) => {
      const detail = err.response?.data?.detail || err.message || '文件格式错误'
      message.error(`导入失败：${detail}`)
    })
    .finally(() => {
      candidateLoading.value = false
    })
  return false  // 阻止 antd 默认上传
}

// 爬虫 CSV 导入（原有）
function handleBeforeUpload(file) {
  result.value = null
  kolApi.importCsv(file)
    .then((data) => {
      result.value = data
      message.success(`导入完成: ${data.imported} 条`)
    })
    .catch(() => {
      message.error('导入失败,请检查文件格式')
    })
  return false
}

function downloadTemplate() {
  const csv = `name,email,channel_id,channel_url,subscribers,country,niche,recent_videos
MrBeast,contact@mrbeast.com,UCX6OQ3DkcsbYNE6H8uQQuVA,https://youtube.com/@MrBeast,320000000,US,entertainment,I Built a City in 24 Hours|I Spent 50 Hours In Solitary
MKBHD,business@mkbhd.com,UCBcRF18a7Qf58cCRy5xuWwQ,https://youtube.com/@mkbhd,19000000,US,tech,iPhone 16 Pro Review|The New MacBook Pro`
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'kol_template.csv'
  a.click()
  URL.revokeObjectURL(url)
}
</script>

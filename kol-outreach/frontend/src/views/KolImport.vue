<template>
  <div>
    <a-alert
      type="info"
      show-icon
      style="margin-bottom: 24px"
      message="CSV 格式说明"
    >
      <template #description>
        <p style="margin: 4px 0"><b>必填列:</b> name, email</p>
        <p style="margin: 4px 0"><b>可选列:</b> channel_id, channel_url, subscribers, country, niche, recent_videos</p>
        <p style="margin: 4px 0"><b>recent_videos</b> 多个标题用 <code>|</code> 分隔,例:<code>视频1|视频2|视频3</code></p>
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
      style="margin-top: 24px"
      :status="result.imported > 0 ? 'success' : 'warning'"
      :title="`导入完成:成功 ${result.imported} 条,跳过 ${result.skipped} 条`"
    />

    <a-button style="margin-top: 16px" @click="downloadTemplate">
      <download-outlined /> 下载 CSV 模板
    </a-button>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { message } from 'ant-design-vue'
import { InboxOutlined, DownloadOutlined } from '@ant-design/icons-vue'
import { kolApi } from '../api'

const result = ref(null)

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
  return false  // 阻止 antd 默认上传
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

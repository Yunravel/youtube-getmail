<template>
  <div class="auto-reply-page">
    <a-page-header title="报价自动回复" sub-title="活动模板、发送开关与安全规则" />
    <AutoReplySettings />
  </div>
</template>

<script setup>
import AutoReplySettings from '../components/AutoReplySettings.vue'
</script>

<style scoped>
.auto-reply-page {
  max-width: 1100px;
  margin: 0 auto;
}
</style>

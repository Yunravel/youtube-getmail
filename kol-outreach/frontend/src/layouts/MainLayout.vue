<template>
  <a-layout style="min-height: 100vh">
    <a-layout-sider
      v-model:collapsed="collapsed"
      collapsible
      breakpoint="lg"
      :collapsed-width="64"
    >
      <div class="logo">
        <span v-if="!collapsed">外联回信中台</span>
        <span v-else>K</span>
      </div>
      <a-menu
        v-model:selectedKeys="selectedKeys"
        theme="dark"
        mode="inline"
        @click="onMenuClick"
      >
        <a-menu-item key="/dashboard">
          <dashboard-outlined />
          <span>总览</span>
        </a-menu-item>
        <a-menu-item key="/hot-leads">
          <fire-outlined />
          <span>Hot Lead</span>
        </a-menu-item>
        <a-menu-item key="/mailbox">
          <mail-outlined />
          <span>邮箱</span>
        </a-menu-item>
        <a-menu-item key="/kols">
          <team-outlined />
          <span>KOL 列表</span>
        </a-menu-item>
        <a-menu-item key="/kol-import">
          <upload-outlined />
          <span>导入 KOL</span>
        </a-menu-item>
        <a-menu-item key="/crawler">
          <radar-chart-outlined />
          <span>采集 KOL</span>
        </a-menu-item>
        <a-menu-item key="/mailbox-settings">
          <setting-outlined />
          <span>邮箱配置</span>
        </a-menu-item>
        <a-menu-item key="/stats">
          <bar-chart-outlined />
          <span>统计</span>
        </a-menu-item>
      </a-menu>
    </a-layout-sider>

    <a-layout>
      <a-layout-header style="background: #fff; padding: 0 24px">
        <h3 style="margin: 0">{{ currentTitle }}</h3>
      </a-layout-header>
      <a-layout-content class="main-content">
        <div class="page-shell" :class="{ 'page-shell-full': route.meta.fullBleed }">
          <router-view />
        </div>
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  DashboardOutlined,
  FireOutlined,
  MailOutlined,
  TeamOutlined,
  UploadOutlined,
  RadarChartOutlined,
  BarChartOutlined,
  SettingOutlined,
} from '@ant-design/icons-vue'

const route = useRoute()
const router = useRouter()
const collapsed = ref(false)
const selectedKeys = ref([route.path])

const currentTitle = computed(() => route.meta.title || 'KOL 外联中台')

watch(() => [route.path, route.query.from], ([p, from]) => {
  // 从邮箱进入会话详情时继续高亮邮箱入口。
  if (p.startsWith('/threads')) selectedKeys.value = [from === 'mailbox' ? '/mailbox' : '/hot-leads']
  else selectedKeys.value = [p]
}, { immediate: true })

function onMenuClick({ key }) {
  router.push(key)
}
</script>

<style scoped>
.logo {
  height: 48px;
  color: #fff;
  font-size: 18px;
  font-weight: bold;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.1);
  margin: 8px;
  border-radius: 6px;
}

.main-content {
  margin: 16px;
}

.page-shell {
  min-height: 360px;
  padding: 24px;
  overflow: hidden;
  background: #fff;
  border-radius: 8px;
}

.page-shell-full {
  min-height: calc(100vh - 96px);
  padding: 0;
}

@media (max-width: 768px) {
  .main-content {
    margin: 8px;
  }

  .page-shell:not(.page-shell-full) {
    padding: 16px;
  }
}
</style>

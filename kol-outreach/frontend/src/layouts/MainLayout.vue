<template>
  <a-layout style="min-height: 100vh">
    <a-layout-sider v-model:collapsed="collapsed" collapsible>
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
        <a-menu-item key="/kols">
          <team-outlined />
          <span>KOL 列表</span>
        </a-menu-item>
        <a-menu-item key="/kol-import">
          <upload-outlined />
          <span>导入 KOL</span>
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
      <a-layout-content style="margin: 16px">
        <div :style="{ background: '#fff', padding: '24px', minHeight: '360px', borderRadius: '8px' }">
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
  TeamOutlined,
  UploadOutlined,
  BarChartOutlined,
} from '@ant-design/icons-vue'

const route = useRoute()
const router = useRouter()
const collapsed = ref(false)
const selectedKeys = ref([route.path])

const currentTitle = computed(() => route.meta.title || 'KOL 外联中台')

watch(() => route.path, (p) => {
  // 子路由(threads/:id)时高亮 Hot Lead
  if (p.startsWith('/threads')) selectedKeys.value = ['/hot-leads']
  else selectedKeys.value = [p]
})

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
</style>

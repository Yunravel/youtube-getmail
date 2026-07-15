import { createRouter, createWebHistory } from 'vue-router'
import { message } from 'ant-design-vue'

const routes = [
  {
    path: '/',
    component: () => import('../layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('../views/Dashboard.vue'),
        meta: { title: '总览' },
      },
      {
        path: 'hot-leads',
        name: 'HotLeads',
        component: () => import('../views/HotLeads.vue'),
        meta: { title: 'Hot Lead 看板' },
      },
      {
        path: 'threads/:id',
        name: 'ThreadDetail',
        component: () => import('../views/ThreadDetail.vue'),
        meta: { title: '会话详情' },
      },
      {
        path: 'kols',
        name: 'KolList',
        component: () => import('../views/KolList.vue'),
        meta: { title: 'KOL 列表' },
      },
      {
        path: 'kol-import',
        name: 'KolImport',
        component: () => import('../views/KolImport.vue'),
        meta: { title: '导入 KOL' },
      },
      {
        path: 'stats',
        name: 'Stats',
        component: () => import('../views/Stats.vue'),
        meta: { title: '统计' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = `${to.meta.title || ''} - 外联回信中台`
})

export default router

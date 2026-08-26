import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import App from './App.vue'
import Browse from './views/Browse.vue'
import Editor from './views/Editor.vue'
import RunView from './views/RunView.vue'
import History from './views/History.vue'
import Settings from './views/Settings.vue'
import GlobalHome from './views/GlobalHome.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Browse },
    { path: '/edit', component: Editor },
    { path: '/run', component: RunView },
    { path: '/history', component: History },
    { path: '/settings', component: Settings },
    { path: '/global', component: GlobalHome },
  ],
})

createApp(App).use(router).use(ElementPlus, { locale: zhCn }).mount('#app')

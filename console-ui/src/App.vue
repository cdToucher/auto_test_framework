<template>
  <el-container style="min-height: 100vh">
    <el-aside width="180px" style="border-right: 1px solid #eee">
      <div style="font-weight: bold; padding: 16px">{{ isGlobal ? 'atk 全局' : 'atk 控制台' }}</div>
      <el-menu router :default-active="$route.path">
        <el-menu-item v-if="isGlobal" index="/global">项目总览</el-menu-item>
        <template v-else>
          <el-menu-item index="/">场景浏览</el-menu-item>
          <el-menu-item index="/run">执行</el-menu-item>
          <el-menu-item index="/history">历史</el-menu-item>
          <el-menu-item index="/settings">设置</el-menu-item>
        </template>
      </el-menu>
    </el-aside>
    <el-main><router-view /></el-main>
  </el-container>
</template>

<script setup>
import { ref } from 'vue'

const isGlobal = ref(false)
fetch('/api/health').then(r => r.json()).then(j => { isGlobal.value = !!j.global })
</script>

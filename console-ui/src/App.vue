<template>
  <el-container style="min-height: 100vh">
    <el-aside width="180px" style="border-right: 1px solid #eee">
      <div style="font-weight: bold; padding: 16px">atk 控制台</div>
      <el-menu router :default-active="$route.path">
        <el-menu-item index="/">场景浏览</el-menu-item>
        <el-menu-item index="/run">执行</el-menu-item>
        <el-menu-item index="/history">历史</el-menu-item>
        <el-menu-item index="/settings">设置</el-menu-item>
      </el-menu>
    </el-aside>
    <el-main><router-view /></el-main>
  </el-container>
</template>

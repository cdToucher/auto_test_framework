<template>
  <el-tabs>
    <el-tab-pane label="环境 environments.yaml">
      <el-input v-model="envRaw" type="textarea" :rows="18" class="mono" />
      <el-button type="primary" style="margin-top: 10px" @click="saveEnv">保存环境配置</el-button>
      <el-alert type="info" :closable="false" style="margin-top: 10px"
        title="敏感值请用 ${env:VAR} 引用环境变量，不要写明文。" />
    </el-tab-pane>
    <el-tab-pane label="模块映射 modules.yaml">
      <el-input v-model="modRaw" type="textarea" :rows="18" class="mono" />
      <el-button type="primary" style="margin-top: 10px" @click="saveMod">保存模块映射</el-button>
    </el-tab-pane>
  </el-tabs>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const envRaw = ref('')
const modRaw = ref('')

onMounted(async () => {
  try {
    envRaw.value = (await api.environments()).raw
    modRaw.value = (await api.modules()).raw
  } catch (e) { console.error(e) }
})

async function saveEnv() {
  try { await api.saveEnvironments(envRaw.value); ElMessage.success('已保存') }
  catch (e) { ElMessage.error(String(e.message || e)) }
}
async function saveMod() {
  try { await api.saveModules(modRaw.value); ElMessage.success('已保存') }
  catch (e) { ElMessage.error(String(e.message || e)) }
}
</script>

<style>
.mono textarea { font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; }
</style>

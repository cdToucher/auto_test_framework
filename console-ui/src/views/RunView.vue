<template>
  <h3>执行</h3>
  <el-form inline>
    <el-form-item label="环境"><el-input v-model="env" style="width: 140px" placeholder="local / demoapp / xinfei" /></el-form-item>
    <el-form-item label="模块"><el-input v-model="mod" style="width: 140px" placeholder="留空=全部" /></el-form-item>
    <el-form-item>
      <el-button type="primary" :loading="running" @click="go" :disabled="running">运行</el-button>
    </el-form-item>
  </el-form>

  <div v-if="logs.length || exitCode !== null"
       class="logbox mono">
    <div v-for="(l, i) in logs" :key="i">{{ l }}</div>
    <div v-if="exitCode !== null" style="margin-top: 8px; font-weight: bold"
         :style="{ color: exitCode === 0 ? '#67c23a' : '#f56c6c' }">
      完成，退出码 {{ exitCode }}（0=通过 1=失败 2=受阻）
      <el-button v-if="lastRunId" size="small" text type="primary"
        @click="$router.push({ path: '/history', query: { run: lastRunId } })">查看历史详情</el-button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const env = ref('local')
const mod = ref('')
const running = ref(false)
const logs = ref([])
const exitCode = ref(null)
const lastRunId = ref(null)

async function go() {
  running.value = true
  logs.value = []
  exitCode.value = null
  try {
    const { job_id } = await api.run(env.value, mod.value || undefined)
    const es = new EventSource(`/api/jobs/${job_id}/stream`)
    es.addEventListener('log', e => {
      logs.value.push(JSON.parse(e.data).line)
      if (logs.value.length > 500) logs.value.splice(0, logs.value.length - 500)
    })
    es.addEventListener('done', e => {
      exitCode.value = JSON.parse(e.data).exit_code
      running.value = false
      es.close()
      setTimeout(() => api.runs().then(rs => { if (rs[0]) lastRunId.value = rs[0].run_id }), 1500)
    })
    es.onerror = () => { if (!running.value) return }
  } catch (e) {
    ElMessage.error(String(e.message || e))
    running.value = false
  }
}
</script>

<style scoped>
.logbox { background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 6px;
  max-height: 480px; overflow: auto; white-space: pre-wrap; }
</style>

<template>
  <h3>运行历史</h3>
  <el-table :data="runs" v-loading="loading" size="small">
    <el-table-column prop="run_id" label="Run ID" min-width="200" />
    <el-table-column prop="created_at" label="时间" width="170" />
    <el-table-column label="结果" width="160">
      <template #default="{ row }">
        <el-tag size="small" type="success">过 {{ row.pass_n }}</el-tag>
        <el-tag size="small" :type="row.fail_n ? 'danger' : 'info'" style="margin-left:4px">败 {{ row.fail_n }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="" width="100">
      <template #default="{ row }">
        <el-button size="small" text type="primary" @click="show(row)">详情</el-button>
      </template>
    </el-table-column>
  </el-table>

  <el-drawer v-model="drawer" :title="cur?.run_id" size="55%">
    <div v-for="(s, si) in cur?.scenarios || []" :key="si" style="margin-bottom: 14px">
      <div>
        <el-tag size="small" :type="verdict(s).type">{{ verdict(s).label }}</el-tag>
        <b style="margin-left: 6px">{{ s.name }}</b>
        <span style="color:#999; margin-left: 8px; font-size: 12px">{{ s.file }} · {{ s.duration_ms }}ms</span>
      </div>
      <div v-for="(st, i) in s.steps" :key="i" style="padding: 4px 0 4px 16px">
        <el-tag size="small" :type="st.passed ? 'success' : 'danger'" effect="plain">{{ st.passed ? '✓' : '✗' }}</el-tag>
        {{ st.title }}
        <div v-if="!st.passed && st.detail" style="color:#f56c6c; font-size:12px; padding-left: 20px">{{ st.detail }}</div>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'

const route = useRoute()
const runs = ref([])
const loading = ref(true)
const drawer = ref(false)
const cur = ref(null)

// error_class 真实取值是 environment 等，不是 blocked；此前判定永不命中
function verdict(s) {
  if (s.passed) return { type: 'success', label: 'PASS' }
  if (s.error_class === 'ui_pending') return { type: 'primary', label: '待实测' }
  if (s.error_class === 'skipped') return { type: 'info', label: '跳过' }
  if (['environment', 'ui_unsupported'].includes(s.error_class)) return { type: 'warning', label: '受阻' }
  return { type: 'danger', label: 'FAIL' }
}

function show(row) { cur.value = row; drawer.value = true }

onMounted(async () => {
  try {
    runs.value = await api.runs()
    if (route.query.run) {
      const hit = runs.value.find(r => r.run_id === route.query.run)
      if (hit) show(hit)
    }
  } catch (e) { console.error(e) }
  loading.value = false
})
</script>

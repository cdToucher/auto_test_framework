<template>
  <h3>项目（全局模式）</h3>
  <el-form inline>
    <el-form-item>
      <el-input v-model="newPath" style="width: 420px" placeholder="输入 atk 工程绝对路径（含 scenarios/）" />
    </el-form-item>
    <el-form-item>
      <el-button type="primary" @click="add">注册项目</el-button>
    </el-form-item>
  </el-form>

  <el-table :data="projects" v-loading="loading" size="small">
    <el-table-column prop="name" label="项目" width="200" />
    <el-table-column prop="path" label="路径" min-width="320" />
    <el-table-column prop="last_seen_at" label="最近使用" width="170" />
    <el-table-column label="" width="220">
      <template #default="{ row }">
        <el-button size="small" type="primary" @click="open(row)">打开控制台</el-button>
        <el-button size="small" type="danger" text @click="del(row)">移除</el-button>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const projects = ref([])
const loading = ref(true)
const newPath = ref('')
const base = ''

async function load() {
  loading.value = true
  const r = await fetch(base + '/api/projects').then(x => x.json())
  projects.value = r.projects || []
  loading.value = false
}

async function add() {
  const r = await fetch(base + '/api/projects', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: newPath.value }),
  })
  const j = await r.json()
  if (!r.ok) { ElMessage.error(j.detail || '注册失败'); return }
  ElMessage.success('已注册')
  newPath.value = ''
  load()
}

async function del(row) {
  await ElMessageBox.confirm(`移除 ${row.name}？（不影响工程文件）`, '确认', { type: 'warning' })
  await fetch(base + `/api/projects?path=${encodeURIComponent(row.path)}`, { method: 'DELETE' })
  load()
}

async function open(row) {
  const r = await fetch(base + '/api/projects/open', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: row.path }),
  })
  const j = await r.json()
  if (!r.ok) { ElMessage.error(j.detail || '启动失败'); return }
  const w = window.open(j.url, '_blank')
  if (!w) {
    ElMessage({ message: '弹窗被拦截，已在本页打开', type: 'info' })
    setTimeout(() => { location.href = j.url }, 600)
  } else {
    ElMessage.success('已启动')
  }
}

onMounted(load)
</script>

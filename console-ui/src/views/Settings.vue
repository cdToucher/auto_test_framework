<template>
  <el-tabs>
    <el-tab-pane label="环境 environments.yaml">
      <el-input v-model="envRaw" type="textarea" :rows="16" class="mono" />
      <el-button type="primary" style="margin-top: 10px" @click="saveEnv">保存环境配置</el-button>
      <el-alert type="info" :closable="false" style="margin-top: 10px"
        title="敏感值请用 ${env:VAR} 引用环境变量，不要写明文。" />
    </el-tab-pane>

    <el-tab-pane label="模块映射 modules.yaml">
      <el-input v-model="modRaw" type="textarea" :rows="16" class="mono" />
      <el-button type="primary" style="margin-top: 10px" @click="saveMod">保存模块映射</el-button>
    </el-tab-pane>

    <el-tab-pane label="定时任务 schedules.yaml">
      <el-alert type="info" :closable="false" style="margin-bottom: 10px"
        title="控制台需保持运行才会触发；停机期间不补跑。触发即执行 atk run，结果进「历史」。" />
      <el-table :data="tasks" size="small">
        <el-table-column label="名称" min-width="130">
          <template #default="{ row }"><el-input v-model="row.name" placeholder="任务名" /></template>
        </el-table-column>
        <el-table-column label="环境" width="110">
          <template #default="{ row }"><el-input v-model="row.env" placeholder="local" /></template>
        </el-table-column>
        <el-table-column label="模块(可空)" width="110">
          <template #default="{ row }"><el-input v-model="row.module" /></template>
        </el-table-column>
        <el-table-column label="cron 或 每天 HH:MM" min-width="170">
          <template #default="{ row }">
            <el-radio-group v-model="row._kind" size="small" style="margin-right:6px">
              <el-radio-button value="daily">每天</el-radio-button>
              <el-radio-button value="cron">cron</el-radio-button>
            </el-radio-group>
            <el-input v-if="row._kind === 'daily'" v-model="row.daily_at" placeholder="02:00" style="width: 90px" />
            <el-input v-else v-model="row.cron" placeholder="0 2 * * *" style="width: 120px" class="mono" />
          </template>
        </el-table-column>
        <el-table-column label="启用" width="70">
          <template #default="{ row }"><el-switch v-model="row.enabled" /></template>
        </el-table-column>
        <el-table-column prop="next_run" label="下次触发" min-width="150" />
        <el-table-column label="" width="140">
          <template #default="{ row, $index }">
            <el-button size="small" text type="primary" :disabled="!row.name || !row.enabled" @click="trigger(row)">立即运行</el-button>
            <el-button size="small" text type="danger" @click="tasks.splice($index, 1)">删</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div style="margin-top: 10px">
        <el-button @click="addTask">+ 新增任务</el-button>
        <el-button type="primary" :loading="savingSched" @click="saveTasks">保存并生效</el-button>
      </div>
    </el-tab-pane>
  </el-tabs>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

const envRaw = ref('')
const modRaw = ref('')
const tasks = ref([])
const savingSched = ref(false)

onMounted(async () => {
  try {
    envRaw.value = (await fetch('/api/environments').then(r => r.json())).raw
    modRaw.value = (await fetch('/api/modules').then(r => r.json())).raw
    tasks.value = (await fetch('/api/schedules').then(r => r.json())).tasks.map(t => ({
      ...t, _kind: t.daily_at ? 'daily' : 'cron', module: t.module || '',
    }))
  } catch (e) { console.error(e) }
})

async function saveEnv() {
  const r = await fetch('/api/environments', { method: 'PUT',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raw: envRaw.value }) })
  r.ok ? ElMessage.success('已保存') : ElMessage.error((await r.json()).detail)
}
async function saveMod() {
  const r = await fetch('/api/modules', { method: 'PUT',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raw: modRaw.value }) })
  r.ok ? ElMessage.success('已保存') : ElMessage.error((await r.json()).detail)
}

function addTask() {
  tasks.value.push({ name: '', env: 'local', module: '', _kind: 'daily',
    daily_at: '02:00', cron: '', enabled: true })
}

function normalize(t) {
  const out = { name: t.name.trim(), env: t.env.trim(), enabled: !!t.enabled }
  if (t.module && t.module.trim()) out.module = t.module.trim()
  if (t._kind === 'daily') out.daily_at = (t.daily_at || '').trim()
  else out.cron = (t.cron || '').trim()
  return out
}

async function saveTasks() {
  savingSched.value = true
  try {
    const payload = tasks.value.map(normalize)
    const r = await fetch('/api/schedules', { method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tasks: payload }) })
    const j = await r.json()
    if (!r.ok) { ElMessage.error(j.detail || '保存失败'); return }
    ElMessage.success('已保存并生效')
    tasks.value = j2tasks(await fetch('/api/schedules').then(x => x.json()))
  } finally { savingSched.value = false }
}

const j2tasks = (j) => j.tasks.map(t => ({ ...t, _kind: t.daily_at ? 'daily' : 'cron', module: t.module || '' }))

async function trigger(row) {
  const r = await fetch(`/api/schedules/${encodeURIComponent(normalize(row).name)}/trigger`,
    { method: 'POST' })
  if (r.status === 409) { ElMessage.warning('已有任务在执行'); return }
  if (!r.ok) { ElMessage.error('触发失败'); return }
  ElMessage.success('已触发，去「历史」查看结果')
}
</script>

<style>
.mono textarea { font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; }
</style>

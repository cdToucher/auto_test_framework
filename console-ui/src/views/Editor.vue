<template>
  <div v-if="!path"><el-empty description="从场景浏览页点击「编辑」进入" /></div>
  <div v-else v-loading="loading">
    <el-page-header @back="$router.push('/')" :content="path" style="margin-bottom: 12px" />

    <el-radio-group v-model="mode" size="small" style="margin-bottom: 12px">
      <el-radio-button value="form">表单模式</el-radio-button>
      <el-radio-button value="yaml">YAML 源码</el-radio-button>
    </el-radio-group>

    <template v-if="mode === 'form'">
      <el-form label-width="80px" inline>
        <el-form-item label="名称"><el-input v-model="form.scenario" style="width: 240px" /></el-form-item>
        <el-form-item label="模块"><el-input v-model="form.module" style="width: 140px" /></el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority" style="width: 90px">
            <el-option v-for="p in ['P0','P1','P2']" :key="p" :value="p" :label="p" />
          </el-select>
        </el-form-item>
        <el-form-item label="环境"><el-input v-model="form.env" style="width: 120px" /></el-form-item>
        <el-form-item label="标签">
          <el-select v-model="form.tags" multiple filterable allow-create default-first-option
            style="width: 220px" placeholder="回车添加">
          </el-select>
        </el-form-item>
      </el-form>

      <h4>步骤（拖拽排序）</h4>
      <draggable v-model="steps" item-key="_key" handle=".drag-handle">
        <template #item="{ element: st, index: i }">
          <el-card shadow="never" style="margin-bottom: 10px">
            <template #header>
              <span class="drag-handle" style="cursor: grab; margin-right: 8px">⠿</span>
              <b>步骤 {{ i + 1 }}</b>
              <el-tag size="small" style="margin-left: 8px">{{ st._type === 'api' ? 'API' : 'UI' }}</el-tag>
              <el-button size="small" type="danger" text style="float: right" @click="steps.splice(i, 1)">删除</el-button>
            </template>

            <template v-if="st._type === 'api'">
              <el-row :gutter="8">
                <el-col :span="5">
                  <el-select v-model="st.method" style="width: 100%">
                    <el-option v-for="m in ['GET','POST','PUT','PATCH','DELETE']" :key="m" :value="m" />
                  </el-select>
                </el-col>
                <el-col :span="19"><el-input v-model="st.url" placeholder="/api/orders" /></el-col>
              </el-row>

              <kv-table title="Headers" :rows="st.headers" />
              <el-form-item label="Body(JSON)" label-width="90px" style="margin-top: 6px">
                <el-input v-model="st.bodyText" type="textarea" :rows="3" class="mono"
                  placeholder='{"page": 1}' />
              </el-form-item>

              <h5>断言</h5>
              <el-table :data="st.expectRows" size="small">
                <el-table-column label="目标" min-width="180">
                  <template #default="{ row }"><el-input v-model="row.key" placeholder="status 或 data.xxx" /></template>
                </el-table-column>
                <el-table-column label="操作符" width="110">
                  <template #default="{ row }">
                    <el-select v-model="row.op">
                      <el-option value="eq" label="等于" /><el-option value="not_null" label="非空" />
                    </el-select>
                  </template>
                </el-table-column>
                <el-table-column label="期望值" min-width="140">
                  <template #default="{ row }">
                    <el-input v-model="row.value" :disabled="row.op === 'not_null'" />
                  </template>
                </el-table-column>
                <el-table-column width="60">
                  <template #default="{ $index }">
                    <el-button text type="danger" @click="st.expectRows.splice($index, 1)">删</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-button size="small" text type="primary" @click="st.expectRows.push({ key: '', op: 'eq', value: '' })">+ 断言</el-button>

              <kv-table title="Capture（变量提取）" :rows="st.captureRows" />
              <el-form-item label="重试次数" label-width="90px" style="margin-top: 6px">
                <el-input-number v-model="st.retries" :min="0" :max="5" />
              </el-form-item>
            </template>

            <template v-else>
              <el-row :gutter="8">
                <el-col :span="7">
                  <el-select v-model="st.action" allow-create filterable style="width: 100%" placeholder="动作 open/click/fill/snapshot/wait/assert_text">
                    <el-option v-for="a in ['open','click','fill','snapshot','wait','assert_text']" :key="a" :value="a" />
                  </el-select>
                </el-col>
                <el-col :span="9"><el-input v-model="st.target" placeholder="target：URL / 选择器 / 文本" /></el-col>
                <el-col :span="8"><el-input v-model="st.value" placeholder="value（fill 的文本等）" /></el-col>
              </el-row>
              <el-form-item label="断言" label-width="60px" style="margin-top: 6px">
                <el-input v-model="st.expect" placeholder="自然语言断言，AI 实测判定" />
              </el-form-item>
            </template>
          </el-card>
        </template>
      </draggable>

      <el-button @click="addStep('api')">+ API 步骤</el-button>
      <el-button @click="addStep('ui')">+ UI 步骤</el-button>
    </template>

    <template v-else>
      <el-input v-model="yamlText" type="textarea" :rows="26" class="mono" />
    </template>

    <div style="margin-top: 14px">
      <el-button type="primary" :loading="saving" @click="save">保存（先校验）</el-button>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import draggable from 'vuedraggable'
import api from '../api'
import KvTable from '../components/KvTable.vue'

const route = useRoute()
const path = ref(route.query.path || '')
const loading = ref(true)
const mode = ref('form')
const saving = ref(false)
const mtime = ref(0)
const form = ref({ scenario: '', module: '', priority: 'P1', env: '', tags: [] })
const steps = ref([])
const yamlText = ref('')
let keySeq = 0

const kvToRows = (o) => Object.entries(o || {}).map(([k, v]) => ({ k: String(k), v: typeof v === 'string' ? v : JSON.stringify(v) }))
const rowsToKv = (rows) => {
  const o = {}
  for (const r of rows || []) if (r.k !== '') o[r.k] = coerce(r.v)
  return o
}
const coerce = (s) => { try { return JSON.parse(s) } catch { return s } }

function stepToView(raw) {
  if (raw.api) {
    const a = raw.api
    const [method, ...rest] = String(a.call || '').split(' ')
    const expectMap = {}
    for (const [k, v] of Object.entries(a.expect || {})) {
      if (k === 'status') expectMap.status = String(v)
      else expectMap[k] = v === 'not_null' ? '__NOT_NULL__' : String(v)
    }
    return {
      _key: ++keySeq, _type: 'api',
      method: method || 'GET', url: rest.join(' '),
      headers: kvToRows(a.headers), bodyText: a.body ? JSON.stringify(a.body, null, 0) : '',
      expectRows: Object.entries(expectMap).map(([k, v]) => ({
        key: k, op: v === '__NOT_NULL__' ? 'not_null' : 'eq', value: v === '__NOT_NULL__' ? '' : v,
      })),
      captureRows: kvToRows(a.capture), retries: a.retries ?? 1,
    }
  }
  const u = raw.ui || {}
  return { _key: ++keySeq, _type: 'ui', action: u.action || '', target: u.target || u.url || '', value: u.value || '', expect: u.expect || '' }
}

function viewToStep(st) {
  if (st._type === 'api') {
    const expect = {}
    for (const r of st.expectRows) {
      if (r.key === '') continue
      if (r.key === 'status') expect.status = Number(r.value)
      else expect[r.key] = r.op === 'not_null' ? 'not_null' : coerce(r.value)
    }
    return {
      api: {
        call: `${st.method} ${st.url}`.trim(),
        headers: rowsToKv(st.headers),
        ...(st.bodyText.trim() ? { body: coerce(st.bodyText) } : {}),
        expect,
        capture: rowsToKv(st.captureRows),
        retries: st.retries ?? 1,
      },
    }
  }
  const ui = { action: st.action }
  if (st.target) ui.target = st.target
  if (st.value) ui.value = st.value
  if (st.expect) ui.expect = st.expect
  return { ui }
}

function addStep(type) {
  steps.value.push(stepToView(type === 'api' ? { api: { call: 'GET /' } } : { ui: { action: 'open' } }))
}

async function load() {
  loading.value = true
  try {
    const d = await api.scenario(path.value)
    mtime.value = d.mtime
    yamlText.value = (await fetch('/api/render', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: d.data }),
    }).then(r => r.json())).raw
    form.value = {
      scenario: d.data.scenario || '', module: d.data.module || '',
      priority: d.data.priority || 'P1', env: d.data.env || '',
      tags: d.data.tags || [],
    }
    steps.value = (d.data.steps || []).map(stepToView)
  } catch (e) {
    ElMessage.error(String(e.message || e))
    mode.value = 'yaml'
  }
  loading.value = false
}

function buildData() {
  const d = {
    scenario: form.value.scenario,
    module: form.value.module || 'default',
    priority: form.value.priority,
    tags: form.value.tags,
    env: form.value.env || 'local',
    steps: steps.value.map(viewToStep),
  }
  return d
}

async function toYamlMode() {
  if (mode.value === 'yaml') {
    const r = await fetch('/api/render', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: buildData() }),
    })
    yamlText.value = (await r.json()).raw
  } else {
    const r = await api.parse(yamlText.value)
    form.value = {
      scenario: r.data.scenario || '', module: r.data.module || '',
      priority: r.data.priority || 'P1', env: r.data.env || '', tags: r.data.tags || [],
    }
    steps.value = (r.data.steps || []).map(stepToView)
  }
}

async function save() {
  saving.value = true
  try {
    let data
    if (mode.value === 'form') data = buildData()
    else data = (await api.parse(yamlText.value)).data

    const v = await api.validate(data)
    if (!v.ok) { ElMessage.error(v.errors.join('；')); return }

    try {
      await api.save(path.value, { data, if_mtime: mtime.value })
      ElMessage.success('已保存')
      load()
    } catch (e) {
      if (e.conflict) {
        const act = await ElMessageBox.confirm(
          '文件已被外部（AI/CLI）修改。强制覆盖将丢弃外部改动。',
          '冲突', { confirmButtonText: '强制覆盖', cancelButtonText: '刷新', type: 'warning' },
        ).catch(() => 'refresh')
        if (act === 'refresh') { await load(); ElMessage.info('已刷新为最新内容') }
        else { await api.save(path.value, { data, force: true }); ElMessage.success('已强制覆盖'); load() }
      } else throw e
    }
  } finally { saving.value = false }
}

watch(mode, toYamlMode)
onMounted(load)
</script>

<style>
.mono textarea { font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; }
</style>

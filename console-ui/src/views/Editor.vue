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
        <el-form-item label="Fixtures"><el-input v-model="form.data" style="width: 220px" placeholder="fixtures/order.yaml" /></el-form-item>
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
                      <el-option v-for="option in expectOps" :key="option.value"
                        :value="option.value" :label="option.label"
                        :disabled="row.key.trim() === 'status' && nullaryOps.includes(option.value)" />
                      <el-option v-if="!expectOps.some(option => option.value === row.op)"
                        :value="row.op" :label="`未知：${row.op}`" />
                    </el-select>
                  </template>
                </el-table-column>
                <el-table-column label="期望值" min-width="140">
                  <template #default="{ row }">
                    <el-input v-model="row.value" :disabled="nullaryOps.includes(row.op)" placeholder="字符串或 JSON" />
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
const form = ref({ scenario: '', module: '', priority: 'P1', env: '', tags: [], data: '' })
const steps = ref([])
const yamlText = ref('')
const rawScenario = ref({})
let keySeq = 0

const expectOps = [
  { value: 'eq', label: '等于' }, { value: 'ne', label: '不等于' },
  { value: 'lt', label: '小于' }, { value: 'lte', label: '小于等于' },
  { value: 'gt', label: '大于' }, { value: 'gte', label: '大于等于' },
  { value: 'contains', label: '包含' }, { value: 'not_contains', label: '不包含' },
  { value: 'regex', label: '正则' }, { value: 'len', label: '长度' },
  { value: 'type', label: '类型' }, { value: 'in', label: '属于' },
  { value: 'not_in', label: '不属于' }, { value: 'startswith', label: '开头匹配' },
  { value: 'endswith', label: '结尾匹配' }, { value: 'not_null', label: '非空' },
  { value: 'is_null', label: '为空' },
]
const nullaryOps = ['not_null', 'is_null']
const symbolOps = { '==': 'eq', '!=': 'ne', '<': 'lt', '<=': 'lte', '>': 'gt', '>=': 'gte' }

const clone = (value) => JSON.parse(JSON.stringify(value || {}))
const kvToRows = (o) => Object.entries(o || {}).map(([k, v]) => ({
  k: String(k), v: typeof v === 'string' ? v : JSON.stringify(v), _valueType: typeof v,
}))
const rowsToKv = (rows) => {
  const o = {}
  for (const r of rows || []) {
    if (r.k === '') continue
    // 既有字符串保持字符串，避免 "true"、"001" 等看似 JSON 的值被无意改型。
    o[r.k] = r._valueType === 'string' ? r.v : coerce(r.v)
  }
  return o
}
const coerce = (s) => { try { return JSON.parse(s) } catch { return s } }
const showValue = (value) => typeof value === 'string' ? value : JSON.stringify(value)
const normalizeOp = (op) => symbolOps[String(op || '').trim()] || String(op || 'eq').trim()

function parseExpectValue(value) {
  if (typeof value !== 'string') return { op: 'eq', value }
  if (value.startsWith('\\')) return { op: 'eq', value: value.slice(1) }
  const trimmed = value.trim()
  if (nullaryOps.includes(trimmed)) return { op: trimmed, value: null }
  const symbol = value.match(/^\s*(<=|>=|!=|==|<|>)\s*(.*)$/s)
  if (symbol) return { op: symbolOps[symbol[1]], value: symbol[2].trim() }
  const keyword = value.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$/s)
  if (!keyword || !expectOps.some(option => option.value === keyword[1])) return { op: 'eq', value }
  const op = keyword[1]
  const rest = keyword[2].trim()
  if (op === 'in' || op === 'not_in') {
    const inner = rest.startsWith('[') && rest.endsWith(']') ? rest.slice(1, -1) : rest
    return { op, value: inner.trim() ? inner.split(',').map(item => coerce(item.trim())) : [] }
  }
  if (op === 'len' && rest.includes('..')) {
    const [min, max] = rest.split('..', 2)
    return { op, value: { min: coerce(min), max: coerce(max) } }
  }
  return { op, value: op === 'len' ? coerce(rest) : rest }
}

function expectToRows(expect) {
  if (Array.isArray(expect)) {
    return expect.map((item) => {
      const isStatus = Object.prototype.hasOwnProperty.call(item || {}, 'status')
      const rawValue = isStatus ? item.status : item?.value
      const parsed = item?.op == null ? parseExpectValue(rawValue) : { op: normalizeOp(item.op), value: rawValue }
      return {
        key: isStatus ? 'status' : String(item?.path || ''), op: parsed.op,
        value: parsed.value == null ? '' : showValue(parsed.value), _valueType: typeof parsed.value,
      }
    })
  }
  return Object.entries(expect || {}).map(([key, value]) => {
    const parsed = parseExpectValue(value)
    return {
      key, op: parsed.op, value: parsed.value == null ? '' : showValue(parsed.value),
      _valueType: typeof parsed.value,
    }
  })
}

function rowsToExpect(rows) {
  return (rows || []).filter(row => row.key.trim()).map((row) => {
    const op = normalizeOp(row.op)
    const value = row._valueType === 'string' ? row.value : coerce(row.value)
    if (row.key.trim() === 'status') return { status: value, op }
    const rule = { path: row.key.trim(), op }
    if (!nullaryOps.includes(op)) rule.value = value
    return rule
  })
}

function stepToView(raw) {
  if (raw.api) {
    const a = raw.api
    const [method, ...rest] = String(a.call || '').split(' ')
    return {
      _key: ++keySeq, _type: 'api', _raw: clone(a),
      method: method || 'GET', url: rest.join(' '),
      headers: kvToRows(a.headers), bodyText: a.body !== undefined && a.body !== null ? JSON.stringify(a.body, null, 0) : '',
      expectRows: expectToRows(a.expect),
      captureRows: kvToRows(a.capture), retries: a.retries ?? 1,
    }
  }
  const u = raw.ui || {}
  const targetKey = Object.prototype.hasOwnProperty.call(u, 'target') ? 'target' : 'url'
  return { _key: ++keySeq, _type: 'ui', _raw: clone(u), targetKey, action: u.action || '', target: u[targetKey] || '', value: u.value || '', expect: u.expect || '' }
}

function viewToStep(st) {
  if (st._type === 'api') {
    const api = clone(st._raw)
    api.call = `${st.method} ${st.url}`.trim()
    api.headers = rowsToKv(st.headers)
    if (st.bodyText.trim()) api.body = coerce(st.bodyText)
    else delete api.body
    api.expect = rowsToExpect(st.expectRows)
    api.capture = rowsToKv(st.captureRows)
    api.retries = st.retries ?? 1
    return {
      api,
    }
  }
  const ui = clone(st._raw)
  ui.action = st.action
  if (st.target) ui[st.targetKey] = st.target
  else delete ui[st.targetKey]
  if (st.value) ui.value = st.value
  else delete ui.value
  if (st.expect) ui.expect = st.expect
  else delete ui.expect
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
    hydrate(d.data)
  } catch (e) {
    ElMessage.error(String(e.message || e))
    mode.value = 'yaml'
  }
  loading.value = false
}

function buildData() {
  const d = clone(rawScenario.value)
  d.scenario = form.value.scenario
  d.priority = form.value.priority
  d.tags = [...form.value.tags]
  if (form.value.module.trim()) d.module = form.value.module.trim()
  else delete d.module
  if (form.value.env.trim()) d.env = form.value.env.trim()
  else delete d.env
  if (form.value.data.trim()) d.data = form.value.data.trim()
  else delete d.data
  d.steps = steps.value.map(viewToStep)
  return d
}

function hydrate(data) {
  rawScenario.value = clone(data)
  form.value = {
    scenario: data.scenario || '', module: data.module || '',
    priority: data.priority || 'P1', env: data.env || '', tags: data.tags || [],
    data: data.data || '',
  }
  steps.value = (data.steps || []).map(stepToView)
}

async function toYamlMode() {
  if (mode.value === 'yaml') {
    const data = buildData()
    rawScenario.value = clone(data)
    const r = await fetch('/api/render', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data }),
    })
    yamlText.value = (await r.json()).raw
  } else {
    const r = await api.parse(yamlText.value)
    hydrate(r.data)
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

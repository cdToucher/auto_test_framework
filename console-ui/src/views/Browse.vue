<template>
  <el-row :gutter="16">
    <el-col :span="6">
      <h3>模块目录</h3>
      <el-tree
        :data="treeData"
        node-key="path"
        :props="{ label: 'name', children: 'children' }"
        @node-click="onDirClick"
      />
    </el-col>
    <el-col :span="18">
      <h3>场景（{{ filtered.length }}）</h3>
      <el-table :data="filtered" v-loading="loading" size="small">
        <el-table-column prop="scenario" label="名称" min-width="180" />
        <el-table-column prop="module" label="模块" width="100" />
        <el-table-column prop="priority" label="优先级" width="80">
          <template #default="{ row }">
            <el-tag :type="row.priority === 'P0' ? 'danger' : row.priority === 'P1' ? 'warning' : 'info'" size="small">{{ row.priority }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="标签" min-width="120">
          <template #default="{ row }">
            <el-tag v-for="t in row.tags || []" :key="t" size="small" style="margin-right: 4px">{{ t }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="" width="90">
          <template #default="{ row }">
            <el-button size="small" type="primary" text @click="$router.push({ path: '/edit', query: { path: row.path } })">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-col>
  </el-row>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const tree = ref({ dirs: [], scenarios: [] })
const loading = ref(true)
const curDir = ref('')

const treeData = computed(() => [
  { name: '全部场景', path: '', children: tree.value.dirs },
])
const filtered = computed(() =>
  tree.value.scenarios.filter(s =>
    !curDir.value || s.path === curDir.value || s.path.startsWith(curDir.value + '/'),
  ),
)

function onDirClick(node) {
  if (node.path !== undefined) curDir.value = node.path
}

onMounted(async () => {
  try {
    tree.value = await api.tree()
  } catch (e) {
    ElMessage.error(String(e.message || e))
  }
  loading.value = false
})
</script>

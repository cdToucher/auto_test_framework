<template>
  <div style="margin-top: 6px">
    <h5>{{ title }}</h5>
    <el-table :data="rows" size="small">
      <el-table-column label="Key" min-width="160">
        <template #default="{ row }"><el-input v-model="row.k" placeholder="key" /></template>
      </el-table-column>
      <el-table-column label="Value" min-width="220">
        <template #default="{ row }"><el-input v-model="row.v" placeholder="value（对象请写 JSON）" /></template>
      </el-table-column>
      <el-table-column width="60">
        <template #default="{ $index }">
          <el-button text type="danger" @click="rows.splice($index, 1)">删</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-button size="small" text type="primary" @click="rows.push({ k: '', v: '' })">+ 添加</el-button>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, default: '' },
  rows: { type: Array, required: true },
})
</script>

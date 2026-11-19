<template>
  <div>
    <div class="hero"><div><h1>实收</h1><p>到货日实际点收行。结案后不可再写；同一实收号重复提交会被拒绝且不改库。</p></div><button @click="load">刷新</button></div>
    <div class="panel">
      <div class="row">
        <input v-model="form.receive_no" placeholder="实收号" />
        <input v-model="form.supplier_code" placeholder="供应商编码" />
        <input v-model="form.sku" placeholder="货号" />
        <input v-model="form.dock_day" placeholder="到货日" />
        <input v-model.number="form.qty" type="number" placeholder="数量" />
        <button @click="add">登记实收</button>
      </div>
      <p class="msg">{{ err }}</p>
      <p class="okmsg">{{ ok }}</p>
      <table>
        <thead><tr><th>实收号</th><th>供应商</th><th>货号</th><th>到货日</th><th>数量</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id"><td>{{ r.receive_no }}</td><td>{{ r.supplier_code }}</td><td>{{ r.sku }}</td><td>{{ r.dock_day }}</td><td>{{ r.qty }}</td></tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h2>被拒提交复查</h2>
      <p v-if="!rejections.length" class="muted">暂无被拒提交。</p>
      <table v-else>
        <thead><tr><th>时间</th><th>单号</th><th>到货日</th><th>原因码</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="r in rejections" :key="r.id"><td>{{ r.attempted_at }}</td><td>{{ r.number }}</td><td>{{ r.dock_day }}</td><td><code>{{ r.code }}</code></td><td>{{ r.message }}</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const rows = ref([])
const rejections = ref([])
const err = ref('')
const ok = ref('')
const form = ref({ receive_no: '', supplier_code: 'SUP-A', sku: '', dock_day: '2026-11-18', qty: 1 })
async function load() {
  err.value = ''
  rows.value = await api('/receives')
  rejections.value = await api('/rejections?leg=receive&limit=20')
}
async function add() {
  err.value = ''; ok.value = ''
  try {
    const r = await api('/receives', { method: 'POST', body: JSON.stringify(form.value) })
    ok.value = `已登记 ${r.number}`
    await load()
  } catch (e) {
    err.value = e.code ? `${e.message}（${e.code}）` : e.message
    await load()
  }
}
onMounted(load)
</script>

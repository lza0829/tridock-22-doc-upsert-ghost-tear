<template>
  <div>
    <div class="hero"><div><h1>回执</h1><p>供应商回执数量，作为三方对齐第三腿。同一回执号重复提交会被拒绝且不改库；若回执成功但库未变，即视为失败。</p></div><button @click="load">刷新</button></div>
    <div class="panel">
      <div class="row">
        <input v-model="form.confirm_no" placeholder="回执号" />
        <input v-model="form.supplier_code" placeholder="供应商编码" />
        <input v-model="form.sku" placeholder="货号" />
        <input v-model="form.dock_day" placeholder="到货日" />
        <input v-model.number="form.qty" type="number" placeholder="数量" />
        <button @click="add">登记回执</button>
      </div>
      <p class="msg">{{ err }}</p>
      <p class="okmsg">{{ ok }}</p>
      <table>
        <thead><tr><th>回执号</th><th>供应商</th><th>货号</th><th>到货日</th><th>数量</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id"><td>{{ r.confirm_no }}</td><td>{{ r.supplier_code }}</td><td>{{ r.sku }}</td><td>{{ r.dock_day }}</td><td>{{ r.qty }}</td></tr>
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
const form = ref({ confirm_no: '', supplier_code: 'SUP-A', sku: '', dock_day: '2026-11-18', qty: 1 })
async function load() {
  err.value = ''
  rows.value = await api('/confirms')
  rejections.value = await api('/rejections?leg=confirm&limit=20')
}
async function add() {
  err.value = ''; ok.value = ''
  try {
    const r = await api('/confirms', { method: 'POST', body: JSON.stringify(form.value) })
    ok.value = `已登记 ${r.number}`
    await load()
  } catch (e) {
    err.value = e.code ? `${e.message}（${e.code}）` : e.message
    await load()
  }
}
onMounted(load)
</script>

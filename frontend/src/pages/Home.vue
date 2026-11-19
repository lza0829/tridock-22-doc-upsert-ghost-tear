<template>
  <div>
    <div class="hero">
      <div>
        <h1>总览</h1>
        <p>当前到货日三腿行数、对账批次与结案状态。结案后当日预报/实收/回执不可再改；重复单号提交被拒，不增加笔数。</p>
      </div>
      <button @click="load">刷新</button>
    </div>
    <div class="grid" v-if="d">
      <div class="stat"><div class="k">到货日</div><div class="v">{{ d.dock_day }}</div></div>
      <div class="stat"><div class="k">预报 / 实收 / 回执</div><div class="v">{{ d.expect_count }} / {{ d.receive_count }} / {{ d.confirm_count }}</div></div>
      <div class="stat"><div class="k">未决差额</div><div class="v">{{ d.pending_variance }}</div></div>
      <div class="stat">
        <div class="k">结案</div>
        <div class="v"><span class="tag" :class="d.sealed ? 'seal' : 'bad'">{{ d.sealed ? '已结案' : '未结案' }}</span></div>
      </div>
      <div class="stat"><div class="k">被拒提交</div><div class="v">{{ d.rejected_count }}</div></div>
    </div>
    <div class="panel" v-if="d && d.latest_batch">
      <h2>最近对账批次</h2>
      <p>
        {{ d.latest_batch.batch_no }} · 对齐 {{ d.latest_batch.matched_count }} · 差额 {{ d.latest_batch.variance_count }} ·
        笔数 {{ d.latest_batch.matched_count + d.latest_batch.variance_count }} ·
        <span class="tag" :class="d.latest_batch.status === 'sealed' ? 'seal' : d.latest_batch.status === 'open' ? 'ok' : 'bad'">{{ d.latest_batch.status }}</span>
      </p>
      <p v-if="d.latest_batch.status === 'stale'" class="msg">批次快照已过期（装载有变化），请到对账页重跑后再结案。</p>
    </div>
    <p class="msg">{{ err }}</p>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const d = ref(null)
const err = ref('')
async function load() {
  err.value = ''
  try { d.value = await api('/dashboard') } catch (e) { err.value = e.message }
}
onMounted(load)
</script>

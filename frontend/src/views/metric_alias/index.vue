<template>
  <section class="page" data-module="metric_alias">
    <header class="page-head">
      <div>
        <h2>口径注册管理</h2>
        <p class="page-desc">维护口径版本的输入、执行约束与结果记录，为口径注册提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记口径版本</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出口径注册清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="alias-layout">
      <div class="alias-main">
        <table class="data-table">
          <thead>
            <tr>
              <th v-for="column in columns" :key="column">{{ column }}</th>
              <th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
              <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
              <td class="row-actions">
                <button class="link" type="button" :data-testid="`detail-${row.id}`" @click="openDetail(row)">详情</button>
                <button
                  v-for="action in actions"
                  :key="action"
                  class="link"
                  type="button"
                  :data-testid="`action-${row.id}-${action}`"
                  @click="runAction(action, row)"
                >
                  {{ action }}
                </button>
              </td>
            </tr>
            <tr v-if="!rows.length">
              <td :colspan="columns.length + 1" class="empty-state">暂无口径注册数据，可先登记口径版本</td>
            </tr>
          </tbody>
        </table>
      </div>

      <aside class="resolution-side" data-testid="resolution-side">
        <h3>口径解析记录</h3>
        <p class="side-desc">按当前时间解析的各指标当前口径，与详情页同源。</p>
        <article
          v-for="item in resolutions"
          :key="String(item['指标编号'])"
          class="resolution-card"
          :data-testid="`resolution-${item['指标编号']}`"
          @click="openResolutionDetail(item)"
        >
          <span class="code">{{ item['指标编号'] }}</span>
          <dl>
            <dt>当前版本</dt>
            <dd>{{ item['当前版本编号'] }}</dd>
            <dt>当前口径</dt>
            <dd>{{ item['当前口径'] }}</dd>
            <dt>生效时间</dt>
            <dd>{{ item['生效时间'] }}</dd>
          </dl>
        </article>
        <p v-if="!resolutions.length" class="empty-state">暂无口径解析记录</p>
      </aside>
    </div>

    <div v-if="detailMetricCode" class="modal-backdrop" data-testid="detail-modal">
      <div class="modal">
        <h3>口径版本详情</h3>
        <dl v-if="detailRow" class="detail-grid">
          <template v-for="column in columns" :key="column">
            <dt>{{ column }}</dt>
            <dd>{{ detailRow[column] ?? '—' }}</dd>
          </template>
          <dt>状态</dt>
          <dd>{{ detailRow.status }}</dd>
        </dl>
        <template v-if="detailResolution">
          <h4>口径解析记录</h4>
          <dl class="detail-grid">
            <dt>指标编号</dt>
            <dd>{{ detailResolution['指标编号'] }}</dd>
            <dt>当前版本编号</dt>
            <dd>{{ detailResolution['当前版本编号'] }}</dd>
            <dt>当前口径</dt>
            <dd>{{ detailResolution['当前口径'] }}</dd>
            <dt>生效时间</dt>
            <dd>{{ detailResolution['生效时间'] }}</dd>
            <dt>解析时间</dt>
            <dd>{{ detailResolution['解析时间'] }}</dd>
            <dt>解析依据</dt>
            <dd>{{ detailResolution['解析依据'] }}</dd>
          </dl>
          <h4>解析历史（含回滚留档）</h4>
          <table class="history-table">
            <thead>
              <tr>
                <th>版本编号</th>
                <th>口径</th>
                <th>生效时间</th>
                <th>状态</th>
                <th>切换原因</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in detailResolution['历史'] ?? []" :key="String(item['版本编号'])">
                <td>{{ item['版本编号'] }}</td>
                <td>{{ item['口径'] }}</td>
                <td>{{ item['生效时间'] }}</td>
                <td>{{ item['状态'] }}</td>
                <td>{{ item['切换原因'] }}</td>
              </tr>
            </tbody>
          </table>
        </template>
        <p v-else class="empty-state">指标 {{ detailMetricCode }} 暂无口径解析记录</p>
        <div class="page-actions">
          <button class="btn" type="button" data-testid="close-detail" @click="closeDetail">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记口径版本</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条口径注册记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Resolution = Record<string, any>

const ENDPOINT = '/api/metric_alias'
const columns = ["版本编号", "指标编号", "变更说明", "旧口径", "新口径", "生效时间", "审批状态", "变更人"]
const actions = ["提交审批", "确认生效", "执行回滚"]
const statuses = ["草稿", "待审批", "已生效", "已回滚"]
const metricNames = ["口径版本总数", "待审批版本", "已生效版本"]

const rows = ref<Row[]>([])
const resolutions = ref<Resolution[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const detailRow = ref<Row | null>(null)
const detailMetricCode = ref('')
// 详情页与列表侧栏共用同一份解析记录，保证两处数据一致
const detailResolution = computed(
  () => resolutions.value.find((item) => item['指标编号'] === detailMetricCode.value) ?? null,
)
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'metric_alias.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '口径注册动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '口径注册操作失败'
  }
}

function openDetail(row: Row) {
  detailRow.value = row
  detailMetricCode.value = String(row['指标编号'] ?? '')
}

function openResolutionDetail(item: Resolution) {
  detailRow.value = rows.value.find((row) => row['指标编号'] === item['指标编号']) ?? null
  detailMetricCode.value = String(item['指标编号'] ?? '')
}

function closeDetail() {
  detailRow.value = null
  detailMetricCode.value = ''
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const [listResponse, resolutionResponse] = await Promise.all([
      request(`${ENDPOINT}?${query}`),
      request(`${ENDPOINT}/resolutions`),
    ])
    if (!listResponse.ok) {
      throw new Error('口径版本列表读取失败')
    }
    const payload = await listResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (resolutionResponse.ok) {
      const resolutionPayload = await resolutionResponse.json()
      resolutions.value = resolutionPayload.items ?? []
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '口径注册列表读取失败'
  }
}

onMounted(reload)
</script>

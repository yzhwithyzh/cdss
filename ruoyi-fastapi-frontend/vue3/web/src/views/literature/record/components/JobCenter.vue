<template>
  <el-col :span="1.5">
    <el-button
      type="primary"
      plain
      icon="Upload"
      @click="openImport"
      v-hasPermi="['literature:job:add']"
      >导入 XML</el-button
    >
  </el-col>
  <el-col :span="1.5">
    <el-badge
      :value="activeCount"
      :hidden="!activeCount"
      type="warning"
    >
      <el-button
        plain
        icon="Cpu"
        @click="openDrawer"
        >判定任务</el-button
      >
    </el-badge>
  </el-col>

  <el-dialog
    title="导入 XML 并判定研究类型"
    v-model="importOpen"
    width="560px"
    append-to-body
    :close-on-click-modal="!uploading"
    :close-on-press-escape="!uploading"
    :show-close="!uploading"
  >
    <el-form label-width="96px">
      <el-form-item label="病种">
        <span class="topic-name">{{ topicName }}</span>
      </el-form-item>
      <el-form-item label="XML 文件">
        <el-upload
          ref="uploadRef"
          class="xml-upload"
          drag
          accept=".xml"
          :limit="1"
          :auto-upload="false"
          :disabled="uploading"
          :on-change="handleFileChange"
          :on-remove="() => (file = null)"
          :on-exceed="handleExceed"
        >
          <el-icon class="el-icon--upload"><upload-filled /></el-icon>
          <div class="el-upload__text">
            将 EndNote / Zotero 导出的 XML 拖到此处，或<em>点击选择</em>
          </div>
        </el-upload>
      </el-form-item>
      <el-form-item label="重复文献">
        <el-checkbox
          v-model="reclassifyExisting"
          :disabled="uploading"
          >库中已有的同一文献也重新判定</el-checkbox
        >
        <div class="form-tip">
          按
          DOI、PMID、标题识别重复。不勾选时重复文献直接跳过；勾选后会重新判定，但已人工确认或修改的结果不会被覆盖。
        </div>
      </el-form-item>
      <el-form-item
        v-if="uploading"
        label="上传进度"
      >
        <el-progress
          :percentage="uploadPercent"
          :stroke-width="10"
          style="width: 100%"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <div class="dialog-footer">
        <el-button
          type="primary"
          :loading="uploading"
          :disabled="!file"
          @click="submitImport"
          >上传并开始</el-button
        >
        <el-button
          :disabled="uploading"
          @click="importOpen = false"
          >取 消</el-button
        >
      </div>
    </template>
  </el-dialog>

  <el-drawer
    v-model="drawerOpen"
    title="导入与判定任务"
    size="620px"
    append-to-body
  >
    <el-alert
      v-if="!workers.length"
      type="warning"
      :closable="false"
      show-icon
      title="当前没有在线的 worker，任务会排队等待"
    >
      <template #default>
        在后端目录运行：<code>.venv/Scripts/python.exe -m module_literature.worker --env=dev</code>
      </template>
    </el-alert>
    <el-alert
      v-else
      type="success"
      :closable="false"
      show-icon
      :title="`worker 在线 ${workers.length} 个`"
    >
      <template #default>
        <div
          v-for="worker in workers"
          :key="worker.workerId"
        >
          {{ worker.workerId }} · {{ worker.model }} · 并发 {{ worker.concurrency }}
          <template v-if="worker.currentJobId"> · 正在处理任务#{{ worker.currentJobId }}</template>
        </div>
      </template>
    </el-alert>

    <el-empty
      v-if="!jobs.length"
      description="还没有导入任务"
    />
    <div
      v-for="job in jobs"
      :key="job.jobId"
      class="job-card"
    >
      <div class="job-head">
        <span class="job-title">#{{ job.jobId }} {{ job.sourceFile }}</span>
        <el-tag
          :type="JOB_STATUS[job.status]?.type"
          size="small"
          >{{ JOB_STATUS[job.status]?.label || job.status }}</el-tag
        >
      </div>
      <p class="job-meta">
        {{ job.createBy }} 于 {{ parseTime(job.createTime) }} 上传 · {{ formatSize(job.fileSize) }}
        <template v-if="job.reclassifyExisting"> · 重复文献重新判定</template>
      </p>

      <template v-if="job.status === 'pending' || job.status === 'parsing'">
        <p class="job-waiting">
          {{ job.status === 'pending' ? '等待 worker 领取…' : '正在解析 XML 并导入题录…' }}
        </p>
      </template>
      <template v-else-if="job.totalCount || job.parsedCount">
        <el-progress
          :percentage="percent(job)"
          :status="job.status === 'completed' ? (job.failed ? 'warning' : 'success') : undefined"
          :stroke-width="10"
        />
        <dl class="job-stats">
          <div>
            <dt>解析</dt>
            <dd>{{ job.parsedCount }}</dd>
          </div>
          <div>
            <dt>新增</dt>
            <dd>{{ job.newCount }}</dd>
          </div>
          <div>
            <dt>重复</dt>
            <dd>{{ job.duplicateCount }}</dd>
          </div>
          <div>
            <dt>已判定</dt>
            <dd>{{ job.done }}</dd>
          </div>
          <div>
            <dt>待判定</dt>
            <dd>{{ job.pending + job.running }}</dd>
          </div>
          <div :class="{ 'is-failed': job.failed }">
            <dt>失败</dt>
            <dd>{{ job.failed }}</dd>
          </div>
        </dl>
        <p class="job-meta">
          模型 {{ job.model || '—' }} · token {{ formatNumber(job.promptTokens) }} 入 /
          {{ formatNumber(job.completionTokens) }} 出
          <template v-if="job.finishedTime"> · 结束于 {{ parseTime(job.finishedTime) }}</template>
        </p>
      </template>

      <el-alert
        v-if="job.errorMessage"
        type="error"
        :closable="false"
        :title="job.errorMessage"
        class="job-error"
      />

      <div
        class="job-actions"
        v-hasPermi="['literature:job:edit']"
      >
        <el-button
          v-for="action in actionsOf(job)"
          :key="action.key"
          :type="action.type"
          size="small"
          plain
          @click="handleAction(job, action)"
          >{{ action.label }}</el-button
        >
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { genFileId } from 'element-plus'
import { changeJob, createJob, listJobs } from '@/api/literature/job'

const props = defineProps({
  topicId: { type: Number, default: undefined },
  topicName: { type: String, default: '' },
})
const emit = defineEmits(['records-changed'])

const { proxy } = getCurrentInstance()

const JOB_STATUS = {
  pending: { label: '排队中', type: 'info' },
  parsing: { label: '解析中', type: 'primary' },
  running: { label: '判定中', type: 'primary' },
  paused: { label: '已暂停', type: 'warning' },
  completed: { label: '已完成', type: 'success' },
  cancelled: { label: '已取消', type: 'info' },
  failed: { label: '失败', type: 'danger' },
}
const ACTIVE = ['pending', 'parsing', 'running']
const ACTIONS = {
  pause: { key: 'pause', label: '暂停', type: 'warning' },
  resume: { key: 'resume', label: '继续', type: 'primary' },
  cancel: {
    key: 'cancel',
    label: '取消',
    type: 'danger',
    confirm: '确认取消该任务？已完成判定的结果会保留。',
  },
  retry: { key: 'retry', label: '重试失败', type: 'primary' },
}

const importOpen = ref(false)
const drawerOpen = ref(false)
const file = ref(null)
const reclassifyExisting = ref(false)
const uploading = ref(false)
const uploadPercent = ref(0)
const jobs = ref([])
const workers = ref([])
let timer = null
let lastActiveIds = new Set()

const activeCount = computed(() => jobs.value.filter((job) => ACTIVE.includes(job.status)).length)

function percent(job) {
  if (!job.totalCount) return job.status === 'completed' ? 100 : 0
  return Math.floor(((job.done + job.failed) / job.totalCount) * 100)
}

function actionsOf(job) {
  const map = {
    pending: ['pause', 'cancel'],
    parsing: ['cancel'],
    running: ['pause', 'cancel'],
    paused: ['resume', 'cancel'],
    cancelled: ['resume'],
    failed: ['retry'],
    completed: job.failed ? ['retry'] : [],
  }
  return (map[job.status] || []).map((key) => ACTIONS[key])
}

function formatNumber(value) {
  return (value || 0).toLocaleString()
}

function formatSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`
}

/** 拉取任务；有任务结束时通知页面刷新列表与统计 */
function loadJobs() {
  return listJobs(props.topicId).then((response) => {
    jobs.value = response.data.jobs
    workers.value = response.data.workers
    const activeIds = new Set(
      jobs.value.filter((job) => ACTIVE.includes(job.status)).map((job) => job.jobId)
    )
    const finished = [...lastActiveIds].some((id) => !activeIds.has(id))
    lastActiveIds = activeIds
    if (finished) emit('records-changed')
    schedule()
  })
}

/** 抽屉打开或有进行中的任务时 3 秒刷新一次，否则 30 秒 */
function schedule() {
  clearTimeout(timer)
  const delay = drawerOpen.value || activeCount.value ? 3000 : 30000
  timer = setTimeout(() => loadJobs().catch(() => schedule()), delay)
}

function openDrawer() {
  drawerOpen.value = true
  loadJobs()
}

function openImport() {
  file.value = null
  reclassifyExisting.value = false
  uploadPercent.value = 0
  importOpen.value = true
  nextTick(() => proxy.$refs.uploadRef?.clearFiles())
}

function handleFileChange(uploadFile) {
  if (!uploadFile.name.toLowerCase().endsWith('.xml')) {
    proxy.$modal.msgError('请选择 XML 文件')
    proxy.$refs.uploadRef.clearFiles()
    file.value = null
    return
  }
  file.value = uploadFile.raw
}

function handleExceed(files) {
  proxy.$refs.uploadRef.clearFiles()
  const next = files[0]
  next.uid = genFileId()
  proxy.$refs.uploadRef.handleStart(next)
}

function submitImport() {
  if (!props.topicId) {
    proxy.$modal.msgError('请先选择病种')
    return
  }
  const formData = new FormData()
  formData.append('file', file.value)
  formData.append('topicId', props.topicId)
  formData.append('reclassifyExisting', reclassifyExisting.value)
  uploading.value = true
  uploadPercent.value = 0
  createJob(formData, (event) => {
    if (event.total)
      uploadPercent.value = Math.min(99, Math.floor((event.loaded / event.total) * 100))
  })
    .then((response) => {
      uploadPercent.value = 100
      proxy.$modal.msgSuccess(`${response.msg}（任务#${response.data.jobId}）`)
      importOpen.value = false
      openDrawer()
    })
    .finally(() => {
      uploading.value = false
    })
}

function handleAction(job, action) {
  const confirmed = action.confirm ? proxy.$modal.confirm(action.confirm) : Promise.resolve()
  confirmed
    .then(() => changeJob(job.jobId, action.key))
    .then((response) => {
      proxy.$modal.msgSuccess(response.msg)
      loadJobs()
    })
    .catch(() => {})
}

watch(
  () => props.topicId,
  (topicId) => {
    if (topicId) loadJobs()
  },
  { immediate: true }
)

onBeforeUnmount(() => clearTimeout(timer))
</script>

<style scoped lang="scss">
.topic-name {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.xml-upload {
  width: 100%;
}

.form-tip {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.job-card {
  padding: 14px 16px;
  margin-top: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.job-head {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
}

.job-title {
  overflow: hidden;
  font-weight: 600;
  color: var(--el-text-color-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-meta,
.job-waiting {
  margin: 6px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.job-waiting {
  font-size: 13px;
  color: var(--el-color-primary);
}

.job-stats {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 4px;
  margin: 10px 0 4px;

  div {
    text-align: center;
  }

  dt {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  dd {
    margin: 2px 0 0;
    font-size: 15px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--el-text-color-primary);
  }

  .is-failed dd {
    color: var(--el-color-danger);
  }
}

.job-error {
  margin-top: 8px;
}

.job-actions {
  margin-top: 10px;
  text-align: right;
}
</style>

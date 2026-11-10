<template>
  <div class="app-container literature-page">
    <el-row :gutter="16">
      <el-col
        :xs="24"
        :sm="7"
        :lg="5"
      >
        <aside class="facet">
          <el-select
            v-model="queryParams.topicId"
            class="facet-topic"
            placeholder="选择病种"
            @change="handleTopicChange"
          >
            <el-option
              v-for="topic in topics"
              :key="topic.topicId"
              :label="topic.topicName"
              :value="topic.topicId"
            />
          </el-select>
          <div class="facet-head">
            <span class="facet-title">研究类型</span>
            <span class="facet-total">{{ facetTotal.toLocaleString() }} 篇</span>
          </div>
          <el-scrollbar
            v-loading="facetLoading"
            class="facet-scroll"
          >
            <ul class="facet-list">
              <li
                class="facet-item"
                :class="{ 'is-active': !queryParams.designLabel }"
                @click="selectDesign(undefined)"
              >
                <span class="facet-label">全部</span>
                <span class="facet-count">{{ facetTotal.toLocaleString() }}</span>
              </li>
              <li
                v-for="item in designLabels"
                :key="item.designLabel"
                class="facet-item"
                :class="{ 'is-active': queryParams.designLabel === item.designLabel }"
                @click="selectDesign(item.designLabel)"
              >
                <span class="facet-label">{{ item.designLabel }}</span>
                <span class="facet-count">{{ item.count.toLocaleString() }}</span>
              </li>
              <li
                v-if="noDesignCount > 0"
                class="facet-item is-muted"
                :class="{ 'is-active': queryParams.designLabel === NO_DESIGN }"
                @click="selectDesign(NO_DESIGN)"
              >
                <span class="facet-label">未判定</span>
                <span class="facet-count">{{ noDesignCount }}</span>
              </li>
            </ul>
          </el-scrollbar>
        </aside>
      </el-col>

      <el-col
        :xs="24"
        :sm="17"
        :lg="19"
      >
        <el-form
          ref="queryRef"
          :model="queryParams"
          :inline="true"
          v-show="showSearch"
        >
          <el-form-item
            label="关键词"
            prop="keyword"
          >
            <el-input
              v-model="queryParams.keyword"
              placeholder="标题或摘要"
              clearable
              style="width: 220px"
              @keyup.enter="handleQuery"
            />
          </el-form-item>
          <el-form-item
            label="编号"
            prop="sourceId"
          >
            <el-input
              v-model="queryParams.sourceId"
              placeholder="来源编号"
              clearable
              style="width: 110px"
              @keyup.enter="handleQuery"
            />
          </el-form-item>
          <el-form-item label="年份">
            <el-input-number
              v-model="queryParams.beginYear"
              :controls="false"
              :min="1900"
              :max="2100"
              placeholder="起"
              style="width: 76px"
            />
            <span class="year-sep">–</span>
            <el-input-number
              v-model="queryParams.endYear"
              :controls="false"
              :min="1900"
              :max="2100"
              placeholder="止"
              style="width: 76px"
            />
          </el-form-item>
          <el-form-item
            label="审核"
            prop="reviewStatus"
          >
            <el-select
              v-model="queryParams.reviewStatus"
              placeholder="全部"
              clearable
              style="width: 100px"
            >
              <el-option
                v-for="(item, key) in REVIEW_STATUS"
                :key="key"
                :label="item.label"
                :value="key"
              />
            </el-select>
          </el-form-item>
          <el-form-item
            label="语言"
            prop="language"
          >
            <el-select
              v-model="queryParams.language"
              placeholder="全部"
              clearable
              style="width: 100px"
            >
              <el-option
                label="中文"
                value="zh"
              />
              <el-option
                label="外文"
                value="en"
              />
            </el-select>
          </el-form-item>
          <el-form-item
            label="全文"
            prop="hasPdf"
          >
            <el-select
              v-model="queryParams.hasPdf"
              placeholder="全部"
              clearable
              style="width: 90px"
            >
              <el-option
                label="有"
                value="1"
              />
              <el-option
                label="无"
                value="0"
              />
            </el-select>
          </el-form-item>
          <el-form-item
            label="摘要"
            prop="hasAbstract"
          >
            <el-select
              v-model="queryParams.hasAbstract"
              placeholder="全部"
              clearable
              style="width: 90px"
            >
              <el-option
                label="有"
                value="1"
              />
              <el-option
                label="无"
                value="0"
              />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              icon="Search"
              @click="handleQuery"
              >搜索</el-button
            >
            <el-button
              icon="Refresh"
              @click="resetQuery"
              >重置</el-button
            >
          </el-form-item>
        </el-form>

        <el-row
          :gutter="10"
          class="mb8"
        >
          <job-center
            :topic-id="queryParams.topicId"
            :topic-name="currentTopicName"
            @records-changed="refreshRecords"
          />
          <el-col :span="1.5">
            <el-button
              type="success"
              plain
              icon="Edit"
              :disabled="single"
              @click="handleUpdate()"
              v-hasPermi="['literature:record:edit']"
              >修改</el-button
            >
          </el-col>
          <el-col :span="1.5">
            <el-button
              type="danger"
              plain
              icon="Delete"
              :disabled="multiple"
              @click="handleDelete()"
              v-hasPermi="['literature:record:remove']"
              >删除</el-button
            >
          </el-col>
          <el-col
            :span="1.5"
            class="result-summary"
          >
            共 <strong>{{ total.toLocaleString() }}</strong> 篇
            <el-tag
              v-if="queryParams.designLabel"
              closable
              size="small"
              @close="selectDesign(undefined)"
            >
              {{ queryParams.designLabel === NO_DESIGN ? '未判定' : queryParams.designLabel }}
            </el-tag>
          </el-col>
          <right-toolbar
            v-model:showSearch="showSearch"
            @queryTable="getList"
          ></right-toolbar>
        </el-row>

        <el-table
          v-loading="loading"
          :data="recordList"
          row-key="recordId"
          @row-dblclick="openDetail"
          @selection-change="handleSelectionChange"
        >
          <el-table-column
            type="selection"
            width="44"
            align="center"
          />
          <el-table-column
            label="编号"
            prop="sourceId"
            width="76"
            align="center"
          />
          <el-table-column
            label="标题"
            min-width="240"
          >
            <template #default="scope">
              <div
                class="title-cell"
                @click="openDetail(scope.row)"
              >
                <span class="title-text">{{ scope.row.title }}</span>
                <span
                  v-if="scope.row.authors"
                  class="title-authors"
                  >{{ scope.row.authors }}</span
                >
              </div>
            </template>
          </el-table-column>
          <el-table-column
            label="年份"
            prop="pubYear"
            width="70"
            align="center"
          />
          <el-table-column
            label="研究类型"
            width="150"
          >
            <template #default="scope">
              <template v-if="scope.row.designLabel">
                <el-tag
                  :type="scope.row.designMethod === 'manual' ? 'warning' : 'primary'"
                  effect="plain"
                  disable-transitions
                  >{{ scope.row.designLabel }}</el-tag
                >
              </template>
              <span
                v-else
                class="muted"
                >未判定</span
              >
            </template>
          </el-table-column>
          <el-table-column
            label="审核"
            width="84"
            align="center"
          >
            <template #default="scope">
              <el-tag
                v-if="scope.row.reviewStatus"
                :type="REVIEW_STATUS[scope.row.reviewStatus]?.type"
                size="small"
                disable-transitions
                >{{ REVIEW_STATUS[scope.row.reviewStatus]?.label }}</el-tag
              >
            </template>
          </el-table-column>
          <el-table-column
            label="全文"
            width="64"
            align="center"
          >
            <template #default="scope">
              <el-link
                v-if="scope.row.hasPdf"
                type="primary"
                underline="never"
                @click="openPdf(scope.row.recordId)"
                >PDF</el-link
              >
              <span
                v-else
                class="muted"
                >—</span
              >
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="215"
            fixed="right"
            align="center"
            class-name="small-padding fixed-width"
          >
            <template #default="scope">
              <el-button
                link
                type="primary"
                icon="View"
                @click="openDetail(scope.row)"
                v-hasPermi="['literature:record:query']"
                >详情</el-button
              >
              <el-button
                link
                type="primary"
                icon="Edit"
                @click="handleUpdate(scope.row)"
                v-hasPermi="['literature:record:edit']"
                >修改</el-button
              >
              <el-button
                link
                type="primary"
                icon="Delete"
                @click="handleDelete(scope.row)"
                v-hasPermi="['literature:record:remove']"
                >删除</el-button
              >
            </template>
          </el-table-column>
        </el-table>

        <pagination
          v-show="total > 0"
          :total="total"
          v-model:page="queryParams.pageNum"
          v-model:limit="queryParams.pageSize"
          @pagination="getList"
        />
      </el-col>
    </el-row>

    <el-drawer
      v-model="detailOpen"
      size="56%"
      append-to-body
      class="literature-drawer"
    >
      <template #header>
        <div class="drawer-head">
          <span class="drawer-index">{{ detail ? `编号 ${detail.sourceId}` : '' }}</span>
          <el-button-group>
            <el-button
              size="small"
              icon="ArrowLeft"
              :disabled="currentIndex <= 0"
              @click="stepDetail(-1)"
              >上一篇</el-button
            >
            <el-button
              size="small"
              :disabled="currentIndex < 0 || currentIndex >= recordList.length - 1"
              @click="stepDetail(1)"
              >下一篇<el-icon class="el-icon--right"><ArrowRight /></el-icon
            ></el-button>
          </el-button-group>
        </div>
      </template>

      <div
        v-loading="detailLoading"
        class="detail"
      >
        <template v-if="detail">
          <h2 class="detail-title">{{ detail.title }}</h2>
          <p class="detail-meta">
            <span v-if="detail.pubYear">{{ detail.pubYear }}</span>
            <span>{{ detail.language === 'zh' ? '中文' : '外文' }}</span>
            <span v-if="detail.topicName">{{ detail.topicName }}</span>
            <span v-if="detail.sourceDb">{{ detail.sourceDb }}</span>
          </p>
          <div class="detail-actions">
            <el-button
              v-if="detail.hasPdf"
              type="primary"
              icon="Document"
              @click="openPdf(detail.recordId)"
              >查看全文</el-button
            >
            <el-button
              v-if="detail.doi"
              icon="Link"
              @click="openExternal(`https://doi.org/${detail.doi}`)"
              >DOI</el-button
            >
            <el-button
              v-if="detail.pmid"
              icon="Link"
              @click="openExternal(`https://pubmed.ncbi.nlm.nih.gov/${detail.pmid}/`)"
              >PubMed</el-button
            >
            <el-button
              icon="Edit"
              @click="handleUpdate(detail)"
              v-hasPermi="['literature:record:edit']"
              >编辑题录</el-button
            >
          </div>

          <section class="detail-section">
            <h3 class="section-title">研究类型</h3>
            <div
              v-if="currentDesign"
              class="design-current"
            >
              <div class="design-main">
                <span class="design-label">{{ currentDesign.designLabel }}</span>
                <el-tag
                  :type="REVIEW_STATUS[currentDesign.reviewStatus]?.type"
                  size="small"
                  >{{ REVIEW_STATUS[currentDesign.reviewStatus]?.label }}</el-tag
                >
              </div>
              <p class="design-source">
                {{ METHOD_TEXT[currentDesign.method] || currentDesign.method }}
                <template v-if="currentDesign.source"> · {{ currentDesign.source }}</template>
                <template v-if="currentDesign.model"> · {{ currentDesign.model }}</template>
                <template v-if="currentDesign.confidence != null">
                  · 置信度 {{ Number(currentDesign.confidence).toFixed(2) }}
                </template>
                <template v-if="currentDesign.reviewedBy">
                  · {{ currentDesign.reviewedBy }} 于
                  {{ parseTime(currentDesign.reviewedTime) }} 审核
                </template>
              </p>
              <p
                v-if="currentDesign.rationale"
                class="design-rationale"
              >
                {{ currentDesign.rationale }}
              </p>
            </div>
            <p
              v-else
              class="muted"
            >
              尚未判定研究类型。
            </p>

            <div
              v-if="!editing"
              class="design-buttons"
              v-hasPermi="['literature:design:review']"
            >
              <el-button
                v-if="currentDesign"
                type="success"
                plain
                icon="Check"
                :disabled="currentDesign.reviewStatus === '1'"
                @click="confirmDesign"
                >{{ currentDesign.reviewStatus === '1' ? '已确认' : '确认' }}</el-button
              >
              <el-button
                type="warning"
                plain
                icon="Edit"
                @click="startEdit"
                >{{ currentDesign ? '修改' : '填写' }}</el-button
              >
            </div>
            <el-form
              v-else
              ref="reviewRef"
              :model="reviewForm"
              :rules="reviewRules"
              label-width="80px"
              class="design-form"
            >
              <el-form-item
                label="研究类型"
                prop="designLabel"
              >
                <el-select
                  v-model="reviewForm.designLabel"
                  filterable
                  allow-create
                  default-first-option
                  placeholder="选择或输入研究类型"
                  style="width: 100%"
                >
                  <el-option
                    v-for="item in designLabels"
                    :key="item.designLabel"
                    :label="item.designLabel"
                    :value="item.designLabel"
                  />
                </el-select>
              </el-form-item>
              <el-form-item
                label="修改理由"
                prop="rationale"
              >
                <el-input
                  v-model="reviewForm.rationale"
                  type="textarea"
                  :rows="2"
                  maxlength="2000"
                  placeholder="如：方法部分为前瞻性队列，非横断面研究"
                />
              </el-form-item>
              <el-form-item>
                <el-button
                  type="primary"
                  :loading="saving"
                  @click="submitModify"
                  >保存</el-button
                >
                <el-button @click="editing = false">取消</el-button>
              </el-form-item>
            </el-form>

            <el-timeline
              v-if="historyDesigns.length"
              class="design-history"
            >
              <el-timeline-item
                v-for="item in historyDesigns"
                :key="item.designId"
                :timestamp="parseTime(item.createTime)"
                type="info"
                size="small"
              >
                <span class="history-label">{{ item.designLabel }}</span>
                <span class="muted">
                  · {{ METHOD_TEXT[item.method] || item.method }}（已被替换）</span
                >
              </el-timeline-item>
            </el-timeline>
          </section>

          <section class="detail-section">
            <h3 class="section-title">摘要</h3>
            <p
              v-if="detail.abstract"
              class="abstract"
            >
              {{ detail.abstract }}
            </p>
            <p
              v-else
              class="muted"
            >
              无摘要
            </p>
          </section>

          <section
            v-for="pico in detail.picos"
            :key="pico.source"
            class="detail-section"
          >
            <h3 class="section-title">
              PICO
              <span class="section-note"
                >{{ METHOD_TEXT[pico.method] || pico.method }} · {{ pico.source }}</span
              >
            </h3>
            <el-descriptions
              :column="1"
              border
              size="small"
            >
              <el-descriptions-item label="研究对象 P">{{
                pico.participants || '—'
              }}</el-descriptions-item>
              <el-descriptions-item label="干预/暴露 I">{{
                pico.intervention || '—'
              }}</el-descriptions-item>
              <el-descriptions-item label="对照 C">{{
                pico.comparison || '—'
              }}</el-descriptions-item>
              <el-descriptions-item label="结局 O">{{ pico.outcomes || '—' }}</el-descriptions-item>
            </el-descriptions>
          </section>

          <section class="detail-section">
            <h3 class="section-title">题录信息</h3>
            <el-descriptions
              :column="2"
              border
              size="small"
            >
              <el-descriptions-item
                label="作者"
                :span="2"
                >{{ detail.authors || '—' }}</el-descriptions-item
              >
              <el-descriptions-item label="DOI">{{ detail.doi || '—' }}</el-descriptions-item>
              <el-descriptions-item label="PMID">{{ detail.pmid || '—' }}</el-descriptions-item>
              <el-descriptions-item label="来源编号">{{ detail.sourceId }}</el-descriptions-item>
              <el-descriptions-item label="来源行号">{{
                detail.sourceRow || '—'
              }}</el-descriptions-item>
              <el-descriptions-item
                v-if="detail.remark"
                label="备注"
                :span="2"
                >{{ detail.remark }}</el-descriptions-item
              >
            </el-descriptions>
          </section>
        </template>
      </div>
    </el-drawer>

    <el-dialog
      title="修改文献题录"
      v-model="editOpen"
      width="760px"
      append-to-body
      :close-on-click-modal="false"
    >
      <el-form
        ref="editRef"
        v-loading="editLoading"
        :model="editForm"
        :rules="editRules"
        label-width="80px"
      >
        <el-form-item
          label="标题"
          prop="title"
        >
          <el-input
            v-model="editForm.title"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
          />
        </el-form-item>
        <el-form-item
          label="作者"
          prop="authors"
        >
          <el-input
            v-model="editForm.authors"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 4 }"
          />
        </el-form-item>
        <el-row>
          <el-col :span="8">
            <el-form-item
              label="年份"
              prop="pubYear"
            >
              <el-input-number
                v-model="editForm.pubYear"
                :controls="false"
                :min="1800"
                :max="2100"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="16">
            <el-form-item
              label="语言"
              prop="language"
            >
              <el-radio-group v-model="editForm.language">
                <el-radio value="zh">中文</el-radio>
                <el-radio value="en">外文</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row>
          <el-col :span="14">
            <el-form-item
              label="DOI"
              prop="doi"
            >
              <el-input
                v-model="editForm.doi"
                maxlength="255"
              />
            </el-form-item>
          </el-col>
          <el-col :span="10">
            <el-form-item
              label="PMID"
              prop="pmid"
            >
              <el-input
                v-model="editForm.pmid"
                maxlength="20"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item
          label="摘要"
          prop="abstract"
        >
          <el-input
            v-model="editForm.abstract"
            type="textarea"
            :autosize="{ minRows: 6, maxRows: 14 }"
          />
        </el-form-item>
        <el-form-item
          label="备注"
          prop="remark"
        >
          <el-input
            v-model="editForm.remark"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 3 }"
            maxlength="500"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button
            type="primary"
            :loading="editSaving"
            @click="submitEdit"
            >确 定</el-button
          >
          <el-button @click="editOpen = false">取 消</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup name="LiteratureRecord">
import {
  delRecord,
  getRecord,
  getRecordPdf,
  listDesignLabels,
  listRecord,
  listTopics,
  reviewDesign,
  updateRecord,
} from '@/api/literature/record'
import JobCenter from './components/JobCenter.vue'

const { proxy } = getCurrentInstance()

const NO_DESIGN = '__none__'
const REVIEW_STATUS = {
  0: { label: '待审核', type: 'info' },
  1: { label: '已确认', type: 'success' },
  2: { label: '已修改', type: 'warning' },
}
const METHOD_TEXT = { import: '导入', llm: '模型判定', manual: '人工' }

const topics = ref([])
const designLabels = ref([])
const noDesignCount = ref(0)
const facetLoading = ref(false)
const recordList = ref([])
const loading = ref(true)
const showSearch = ref(true)
const total = ref(0)

const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const editing = ref(false)
const saving = ref(false)

const selectedRows = ref([])
const single = ref(true)
const multiple = ref(true)
const editOpen = ref(false)
const editLoading = ref(false)
const editSaving = ref(false)
const editForm = ref({})
const editRules = {
  title: [{ required: true, message: '标题不能为空', trigger: 'blur' }],
}

const data = reactive({
  queryParams: {
    pageNum: 1,
    pageSize: 20,
    topicId: undefined,
    keyword: undefined,
    sourceId: undefined,
    designLabel: undefined,
    reviewStatus: undefined,
    language: undefined,
    hasPdf: undefined,
    hasAbstract: undefined,
    beginYear: undefined,
    endYear: undefined,
  },
  reviewForm: { designLabel: undefined, rationale: undefined },
  reviewRules: {
    designLabel: [{ required: true, message: '请选择或输入研究类型', trigger: 'change' }],
  },
})
const { queryParams, reviewForm, reviewRules } = toRefs(data)

const facetTotal = computed(
  () => designLabels.value.reduce((sum, item) => sum + item.count, 0) + noDesignCount.value
)
const currentTopicName = computed(
  () => topics.value.find((topic) => topic.topicId === queryParams.value.topicId)?.topicName || ''
)

/** 导入任务完成后刷新列表与研究类型分布 */
function refreshRecords() {
  getList()
  loadFacets()
}
const currentDesign = computed(() => detail.value?.designs.find((item) => item.isCurrent) || null)
const historyDesigns = computed(() => detail.value?.designs.filter((item) => !item.isCurrent) || [])
const currentIndex = computed(() =>
  detail.value ? recordList.value.findIndex((row) => row.recordId === detail.value.recordId) : -1
)

/** 查询文献列表 */
function getList() {
  loading.value = true
  listRecord(queryParams.value)
    .then((response) => {
      recordList.value = response.rows
      total.value = response.total
    })
    .finally(() => {
      loading.value = false
    })
}

/** 加载研究类型分布（按当前病种，不随其他筛选条件变化） */
function loadFacets() {
  facetLoading.value = true
  const topicId = queryParams.value.topicId
  Promise.all([
    listDesignLabels(topicId),
    listRecord({ topicId, designLabel: NO_DESIGN, pageNum: 1, pageSize: 1 }),
  ])
    .then(([labels, none]) => {
      designLabels.value = labels.data
      noDesignCount.value = none.total
    })
    .finally(() => {
      facetLoading.value = false
    })
}

function handleQuery() {
  queryParams.value.pageNum = 1
  getList()
}

function resetQuery() {
  proxy.resetForm('queryRef')
  queryParams.value.beginYear = undefined
  queryParams.value.endYear = undefined
  queryParams.value.designLabel = undefined
  handleQuery()
}

function selectDesign(label) {
  queryParams.value.designLabel = label
  handleQuery()
}

function handleTopicChange() {
  queryParams.value.designLabel = undefined
  loadFacets()
  handleQuery()
}

/** 打开详情 */
function openDetail(row) {
  editing.value = false
  detailOpen.value = true
  loadDetail(row.recordId)
}

function loadDetail(recordId) {
  detailLoading.value = true
  return getRecord(recordId)
    .then((response) => {
      detail.value = response.data
    })
    .finally(() => {
      detailLoading.value = false
    })
}

function stepDetail(offset) {
  const next = recordList.value[currentIndex.value + offset]
  if (next) openDetail(next)
}

/** 查看全文：带登录凭证获取 PDF 后在新窗口打开 */
function openPdf(recordId) {
  const win = window.open('', '_blank')
  getRecordPdf(recordId)
    .then((blob) => {
      const url = URL.createObjectURL(new Blob([blob], { type: 'application/pdf' }))
      if (win) win.location.href = url
      else window.open(url, '_blank')
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    })
    .catch(() => {
      if (win) win.close()
      proxy.$modal.msgError('全文获取失败')
    })
}

function openExternal(url) {
  window.open(url, '_blank', 'noopener')
}

/** 审核后同步刷新详情、列表行和研究类型分布 */
function afterReview(message) {
  proxy.$modal.msgSuccess(message)
  editing.value = false
  loadDetail(detail.value.recordId)
  getList()
  loadFacets()
}

function confirmDesign() {
  reviewDesign({ recordId: detail.value.recordId, action: 'confirm' }).then((response) =>
    afterReview(response.msg)
  )
}

function startEdit() {
  reviewForm.value = { designLabel: undefined, rationale: undefined }
  editing.value = true
}

function submitModify() {
  proxy.$refs.reviewRef.validate((valid) => {
    if (!valid) return
    saving.value = true
    reviewDesign({
      recordId: detail.value.recordId,
      action: 'modify',
      designLabel: reviewForm.value.designLabel,
      rationale: reviewForm.value.rationale,
    })
      .then((response) => afterReview(response.msg))
      .finally(() => {
        saving.value = false
      })
  })
}

function handleSelectionChange(selection) {
  selectedRows.value = selection
  single.value = selection.length !== 1
  multiple.value = !selection.length
}

/** 修改题录：始终从后端取完整数据（列表行不含摘要等字段） */
function handleUpdate(row) {
  const recordId = row?.recordId ?? selectedRows.value[0]?.recordId
  if (!recordId) return
  editForm.value = {}
  editOpen.value = true
  editLoading.value = true
  getRecord(recordId)
    .then((response) => {
      const { title, authors, pubYear, abstract, language, doi, pmid, remark } = response.data
      editForm.value = { recordId, title, authors, pubYear, abstract, language, doi, pmid, remark }
    })
    .catch(() => {
      editOpen.value = false
    })
    .finally(() => {
      editLoading.value = false
      proxy.$refs.editRef?.clearValidate()
    })
}

function submitEdit() {
  proxy.$refs.editRef.validate((valid) => {
    if (!valid) return
    editSaving.value = true
    updateRecord({ ...editForm.value, pubYear: editForm.value.pubYear ?? null })
      .then((response) => {
        proxy.$modal.msgSuccess(response.msg)
        editOpen.value = false
        getList()
        if (detailOpen.value && detail.value?.recordId === editForm.value.recordId) {
          loadDetail(editForm.value.recordId)
        }
      })
      .finally(() => {
        editSaving.value = false
      })
  })
}

/** 删除题录（逻辑删除：数据保留，列表和统计中不再出现） */
function handleDelete(row) {
  const rows = row ? [row] : selectedRows.value
  if (!rows.length) return
  const target =
    rows.length === 1 ? `编号为 ${rows[0].sourceId} 的文献` : `选中的 ${rows.length} 篇文献`
  proxy.$modal
    .confirm(`确认删除${target}？删除后不再出现在列表和统计中，数据仍保留在数据库。`)
    .then(() => delRecord(rows.map((item) => item.recordId).join(',')))
    .then((response) => {
      proxy.$modal.msgSuccess(response.msg)
      if (detailOpen.value && rows.some((item) => item.recordId === detail.value?.recordId)) {
        detailOpen.value = false
      }
      getList()
      loadFacets()
    })
    .catch(() => {})
}

listTopics().then((response) => {
  topics.value = response.data
  queryParams.value.topicId = topics.value[0]?.topicId
  loadFacets()
  getList()
})
</script>

<style scoped lang="scss">
.facet {
  position: sticky;
  top: 12px;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 140px);
  min-height: 360px;
  padding: 14px 8px 8px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.facet-topic {
  padding: 0 6px;
  margin-bottom: 14px;
}

.facet-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 0 8px 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.facet-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.facet-total {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.facet-scroll {
  flex: 1;
  margin-top: 6px;
}

.facet-list {
  padding: 0;
  margin: 0;
  list-style: none;
}

.facet-item {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  padding: 7px 10px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  cursor: pointer;
  border-radius: 6px;
  transition: background 0.15s ease;

  &:hover {
    background: var(--el-fill-color-light);
  }

  &.is-active {
    font-weight: 600;
    color: var(--el-color-primary);
    background: var(--el-color-primary-light-9);
  }

  &.is-muted .facet-label {
    color: var(--el-text-color-secondary);
  }
}

.facet-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.facet-count {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-secondary);
}

.year-sep {
  margin: 0 6px;
  color: var(--el-text-color-secondary);
}

.result-summary {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 13px;
  color: var(--el-text-color-regular);

  strong {
    color: var(--el-text-color-primary);
  }
}

.title-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  cursor: pointer;

  &:hover .title-text {
    color: var(--el-color-primary);
  }
}

.title-text {
  display: -webkit-box;
  overflow: hidden;
  line-height: 1.5;
  color: var(--el-text-color-primary);
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.title-authors {
  overflow: hidden;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.muted {
  color: var(--el-text-color-placeholder);
}

/* ---------- drawer ---------- */
.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-right: 12px;
}

.drawer-index {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.detail {
  min-height: 200px;
  padding: 0 4px 24px;
}

.detail-title {
  margin: 0 0 8px;
  font-family: 'Noto Serif SC', 'Source Han Serif SC', 'Songti SC', 'STSong', 'SimSun', serif;
  font-size: 20px;
  font-weight: 600;
  line-height: 1.45;
  color: var(--el-text-color-primary);
}

.detail-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin: 0 0 14px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.detail-actions {
  margin-bottom: 8px;
}

.detail-section {
  padding-top: 18px;
  margin-top: 18px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.section-title {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.section-note {
  margin-left: 8px;
  font-size: 12px;
  font-weight: normal;
  color: var(--el-text-color-secondary);
}

.design-current {
  padding: 12px 14px;
  background: var(--el-fill-color-lighter);
  border-left: 3px solid var(--el-color-primary);
  border-radius: 4px;
}

.design-main {
  display: flex;
  gap: 10px;
  align-items: center;
}

.design-label {
  font-size: 17px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.design-source,
.design-rationale {
  margin: 6px 0 0;
  font-size: 12.5px;
  color: var(--el-text-color-secondary);
}

.design-rationale {
  color: var(--el-text-color-regular);
  white-space: pre-line;
}

.design-buttons {
  margin-top: 12px;
}

.design-form {
  padding: 14px 14px 0 0;
  margin-top: 12px;
  border: 1px dashed var(--el-border-color);
  border-radius: 6px;
}

.design-history {
  padding-left: 2px;
  margin-top: 16px;
}

.history-label {
  color: var(--el-text-color-regular);
  text-decoration: line-through;
}

.abstract {
  margin: 0;
  font-size: 14px;
  line-height: 1.85;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
}
</style>

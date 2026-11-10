import request from '@/utils/request'

// 查询病种列表
export function listTopics() {
  return request({
    url: '/literature/record/topics',
    method: 'get',
  })
}

// 查询文献题录列表
export function listRecord(query) {
  return request({
    url: '/literature/record/list',
    method: 'get',
    params: query,
  })
}

// 查询研究类型统计
export function listDesignLabels(topicId) {
  return request({
    url: '/literature/record/designLabels',
    method: 'get',
    params: { topicId },
  })
}

// 查询文献题录详情
export function getRecord(recordId) {
  return request({
    url: '/literature/record/' + recordId,
    method: 'get',
  })
}

// 获取文献全文 PDF
export function getRecordPdf(recordId) {
  return request({
    url: '/literature/record/' + recordId + '/pdf',
    method: 'get',
    responseType: 'blob',
  })
}

// 审核研究类型（确认或修改）
export function reviewDesign(data) {
  return request({
    url: '/literature/record/design/review',
    method: 'put',
    data: data,
  })
}

// 修改文献题录
export function updateRecord(data) {
  return request({
    url: '/literature/record',
    method: 'put',
    data: data,
  })
}

// 删除文献题录（逻辑删除）
export function delRecord(recordIds) {
  return request({
    url: '/literature/record/' + recordIds,
    method: 'delete',
  })
}

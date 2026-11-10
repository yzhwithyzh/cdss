import request from '@/utils/request'

// 上传 XML 并创建导入与研究类型判定任务
export function createJob(formData, onUploadProgress) {
  return request({
    url: '/literature/job',
    method: 'post',
    data: formData,
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 0,
    onUploadProgress,
  })
}

// 查询最近的任务与在线 worker
export function listJobs(topicId) {
  return request({
    url: '/literature/job/list',
    method: 'get',
    params: { topicId },
  })
}

// 控制任务：pause 暂停、resume 继续、cancel 取消、retry 重试失败
export function changeJob(jobId, action) {
  return request({
    url: '/literature/job/' + jobId + '/' + action,
    method: 'put',
  })
}

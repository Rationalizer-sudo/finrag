/**
 * 系统级接口：健康检查与能力清单。
 *
 * 响应格式差异：
 * - `/health`、`/health/live`、`/api` 是探针/自描述端点，返回**裸对象**
 *   （便于容器健康检查与监控直接消费），request.js 拦截器识别到无 `code`
 *   字段时原样返回；
 * - `/capabilities` 属于业务数据，与其他业务接口一致使用
 *   `{code, message, data}` 信封，拦截器会自动解包出 `data`。
 */
import request from './request'

/**
 * 健康检查。
 * @returns {Promise<{status:string,version:string,app_env:string,components:Array}>}
 * status: ok（全部组件正常）/ degraded（MySQL 正常但存在降级组件）/ down
 */
export function getHealth() {
  return request.get('/health', { silent: true })
}

/**
 * 系统能力清单：支持的文件类型、检索模式、评分口径、功能开关、可选依赖。
 * 前端据此自适应（例如上传入口的显隐、下拉选项的数据来源）。
 * 返回值已由拦截器解包，直接就是能力对象。
 */
export function getCapabilities() {
  return request.get('/capabilities', { silent: true })
}

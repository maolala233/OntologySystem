/**
 * 连通性测试相关的前端工具。
 *
 * 这里解决两个真实踩过的问题：
 *
 * 1) 校验范围过大
 *    原来每个测试都调用 `configForm.validateFields()`（无参数）→ 校验**整个表单**。
 *    表单有 9 个必填项，于是"测 Milvus"也会因为"LLM 端点没填"而失败，
 *    表现是五个测试全部报错，且报的是同一个 undefined。
 *    现在每个测试只校验自己那几个字段（见 CONNECTIVITY_FIELDS），互不牵连。
 *
 * 2) 错误文案显示成 undefined
 *    antd 的 `validateFields()` 校验失败时 reject 的是
 *    `{ errorFields, values, outOfDate }`，不是 Error —— 既没有 response
 *    也没有 message，直接拼进模板就成了 "xxx 连通性测试失败：undefined"。
 *    用 describeConnectivityError() 区分"没发起"和"真失败"。
 */

/**
 * 每个测试接口真正读取的字段。
 * 必须与后端 app/api/system.py 里各 test-connectivity 接口读取的 key 一致，
 * 因为 `validateFields(names)` 返回的 values **只包含列出的字段**，
 * 这些 values 会原样 POST 给后端 —— 漏字段后端就收不到。
 */
export type ConnectivityService = 'llm' | 'neo4j' | 'embedding' | 'milvus' | 'vl';

/**
 * 注意：这里必须是可变的 `string[]`，不能写 `as const`。
 * antd 的 validateFields 签名是 `(nameList?: any[], opt?)`，
 * 只读元组（readonly [...]）无法赋值给可变数组，会直接编译报错 TS2769。
 */
export const CONNECTIVITY_FIELDS: Record<ConnectivityService, string[]> = {
  llm: ['api_key', 'base_url', 'model'],
  neo4j: ['neo4j_uri', 'neo4j_username', 'neo4j_password'],
  embedding: ['embedding_api_key', 'embedding_base_url', 'embedding_model'],
  milvus: ['milvus_host', 'milvus_port'],
  vl: ['vl_api_key', 'vl_base_url', 'vl_model'],
};

interface AntdValidateError {
  errorFields?: unknown;
}

interface AxiosLikeError {
  response?: { data?: { message?: string; detail?: string } };
  message?: string;
}

export function describeConnectivityError(service: string, error: unknown): string {
  const err = error as (AntdValidateError & AxiosLikeError) | null | undefined;

  // 表单校验失败：请求根本没发出去，别说成"失败"
  if (err?.errorFields) {
    const names = (err.errorFields as Array<{ name?: unknown[] }>)
      .map((f) => (Array.isArray(f?.name) ? f.name.join('.') : ''))
      .filter(Boolean)
      .join('、');
    return names
      ? `${service} 连通性测试未发起：请先填写 ${names}`
      : `${service} 连通性测试未发起：请先填完相关必填项`;
  }

  // 真实失败：优先用后端返回的 message / FastAPI 的 detail，最后兜底 String(error)
  const detail =
    err?.response?.data?.message ||
    err?.response?.data?.detail ||
    err?.message ||
    String(error);

  return `${service} 连通性测试失败：${detail}`;
}

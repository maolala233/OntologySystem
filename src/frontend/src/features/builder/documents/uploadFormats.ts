// 上传格式约束（与后端 ALLOWED_EXTS 同源；2026-11-09 测试5：说明完整化 + 拖入拦截）
export const UPLOAD_EXTS = [
    'txt', 'md', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'ppt', 'pptx', 'html', 'json',
];
export const UPLOAD_ACCEPT = UPLOAD_EXTS.map((e) => `.${e}`).join(',');

/** 单文件大小上限（MB）——前端校验；后端另有独立上限 */
export const MAX_UPLOAD_MB = 10;
/** 提示文案中的上限描述 */
export const MAX_UPLOAD_LABEL = `${MAX_UPLOAD_MB}MB`;
/** 单文件字节上限 */
export const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024;

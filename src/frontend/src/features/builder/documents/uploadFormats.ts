// 上传格式约束（与后端 ALLOWED_EXTS 同源；2026-11-09 测试5：说明完整化 + 拖入拦截）
export const UPLOAD_EXTS = [
    'txt', 'md', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'ppt', 'pptx', 'html', 'json',
];
export const UPLOAD_ACCEPT = UPLOAD_EXTS.map((e) => `.${e}`).join(',');

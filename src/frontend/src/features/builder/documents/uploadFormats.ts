// 上传格式约束（2026-11-23 收敛）：只放行 Word / Excel / PPT / PDF 四类办公文档，
// txt / md / csv / html / json 等不再进入上传入口。后端 ALLOWED_EXTS 仍保留旧格式，
// 这里作为前端白名单统一拦截（创建项目弹窗、文档页上传、详情板块三处共用）。
export const UPLOAD_EXTS = [
    'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'pdf',
];
export const UPLOAD_ACCEPT = UPLOAD_EXTS.map((e) => `.${e}`).join(',');

/** 面向用户的格式名（弹窗 / 详情板块统一这一句，不再各处手写扩展名串） */
export const UPLOAD_TYPE_LABEL = 'Word / Excel / PPT / PDF';

/** 单文件大小上限（MB）——前端校验；后端另有独立上限 */
export const MAX_UPLOAD_MB = 10;
/** 提示文案中的上限描述 */
export const MAX_UPLOAD_LABEL = `${MAX_UPLOAD_MB}MB`;
/** 单文件字节上限 */
export const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024;

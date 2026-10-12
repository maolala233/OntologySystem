/**
 * 生成 v4 UUID。
 * crypto.randomUUID 只在安全上下文（HTTPS / localhost）可用，明文 HTTP 部署
 * （如 http://IP:3080）下调用会抛 TypeError——改用 getRandomValues 实现，
 * 该 API 在所有上下文均可使用。
 */
export function uuid(): string {
    if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
        bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
        const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0'));
        return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
    }
    // 极老浏览器兜底（不加密，仅作会话分组 id，无安全含义）
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = (Math.random() * 16) | 0;
        return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
    });
}

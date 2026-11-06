/**
 * 质量机检标签（2026-11-05 简化）：纯展示标签——
 * 「质量自检通过」（无硬伤，绿）/「机检 N 处硬伤」（红，发布会被拦截）。
 * 判定=确定性机检（类层次环/悬空 is-a/不可满足类），error=0 即通过；
 * 修复与裁决全部由大模型完成，说明卡不再展示。
 */
import React from 'react';
import { Tag } from 'antd';

interface ChecksSummary {
    passed: boolean;
    error_count: number;
    warning_count: number;
    issues: Array<Record<string, any>>;
}

const QualityCheckTag: React.FC<{ checks?: ChecksSummary | null }> = ({ checks }) => {
    if (!checks) return null;
    const passed = checks.error_count === 0;
    return (
        <Tag color={passed ? 'green' : 'red'} className="cursor-default">
            {passed ? '✓ 质量自检通过' : `机检 ${checks.error_count} 处硬伤`}
        </Tag>
    );
};

export default QualityCheckTag;

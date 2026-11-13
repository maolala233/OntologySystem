/**
 * 本体构建结果卡（2026-11-05 精简定稿）：
 * 单张浅色卡片与系统品牌风格一致——标题+质量自检标签，正文四格彩色统计
 * （浅色底、彩色图标数字），右上角 查看图谱 / 发布到资产中心（一键直发，
 * 发布后翻转 已发布 vN + 取消发布 + 重新发布）。分类明细表与导出已按需求移除。
 */
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { message } from '../../../shared/feedback';
import { Button, Card, Col, Row, Space, Statistic, Tag } from 'antd';
import {
  AppstoreOutlined, CloudUploadOutlined, NodeIndexOutlined,
  PartitionOutlined, SafetyCertificateOutlined, ClusterOutlined,
} from '@ant-design/icons';

import { BuildSummary } from '../../../api/pipeline';
import { publishApi } from '../../../api/governance';
import { publishWithDomain } from './publishFlow';

interface Props {
  projectId: number;
  summary: BuildSummary;
  checks?: BuildSummary['checks'];
  /** 已发布时的版本号（展示"已发布 vN"标签） */
  publishedVersionNo?: number | null;
  onPublishedChange?: () => void;
}

const BuildResultsPanel: React.FC<Props> = ({ projectId, summary, checks, publishedVersionNo, onPublishedChange }) => {
  const navigate = useNavigate();
  const [publishing, setPublishing] = useState(false);
  // 发布状态以后端 is_published 为准；版本号仅取自 publication 快照供展示
  // （单看版本号会把"刚入库的 schema/full 快照"误判成已发布——2026-11-06 实测修复）
  const published = !!summary.is_published && publishedVersionNo != null;

  const doPublish = async () => {
    setPublishing(true);
    try {
      // 无知识域时弹选择器带域发布（2026-11-07：选域从构建期移到发布期）
      const r = await publishWithDomain(projectId);
      if (r) {
        message.success(`发布完成：v${r.version_no} 已进入资产中心`);
        onPublishedChange?.();
      }
    } catch (e: any) {
      const err = e.response?.data?.error || e.response?.data;
      message.error(err?.message || '发布失败，请稍后重试');
    } finally {
      setPublishing(false);
    }
  };

  const doUnpublish = async () => {
    try {
      await publishApi.unpublish(projectId);
      message.success('已取消发布（资产中心不再展示）');
      onPublishedChange?.();
    } catch (e: any) {
      message.error(e.response?.data?.error?.message || '取消发布失败');
    }
  };

  // 统计格：四项指标不再各来一套色相（蓝/紫/青/橙一屏里会显花），
  // 统一品牌淡底，靠图标和等宽数字区分——与工作台统计卡同一套语言
  const statCards = [
    { title: '类', value: summary.schema.classes_total, icon: <AppstoreOutlined /> },
    { title: '关系', value: summary.schema.object_properties_total, icon: <PartitionOutlined /> },
    { title: '实例', value: summary.instances.total, icon: <ClusterOutlined /> },
    { title: '公理', value: summary.axioms.total, icon: <SafetyCertificateOutlined /> },
  ];

  return (
    <Card size="small"
          title={<span className="text-base">本体构建结果</span>}
          extra={(
            <Space wrap>
              {published && (
                <Tag style={{ background: 'rgba(91,141,239,0.12)', color: '#2A57C6', borderColor: 'rgba(91,141,239,0.28)' }}>
                  已发布
                </Tag>
              )}
              <Button size="small" type="primary" ghost icon={<NodeIndexOutlined />}
                      onClick={() => navigate(`/projects/${projectId}/result?tab=graph`)}>
                查看图谱
              </Button>
              {published && (
                <Button size="small" onClick={doUnpublish}>取消发布</Button>
              )}
              <Button size="small" type="primary" icon={<CloudUploadOutlined />}
                      loading={publishing} onClick={doPublish}>
                {published ? '重新发布' : '发布到资产中心'}
              </Button>
            </Space>
          )}>
      <Row gutter={12}>
        {statCards.map((st) => (
          <Col xs={12} sm={6} key={st.title}>
            <div
              className="rounded-xl border border-[#EDF2FA] px-4 py-3"
              style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.06) 0%, #FFFFFF 70%)' }}
            >
              <Statistic
                title={<span className="text-[12px] text-[#66738F]">{st.title}</span>}
                value={st.value}
                prefix={<span style={{ color: '#5B8DEF', marginRight: 6, fontSize: 16 }}>{st.icon}</span>}
                valueStyle={{ color: '#0F172A', fontSize: 24, fontWeight: 700, fontFamily: "'JetBrains Mono', Consolas, monospace" }}
              />
            </div>
          </Col>
        ))}
      </Row>
      {checks && (
        <div className="mt-2">
          {checks.error_count === 0
            ? <Tag style={{ background: 'rgba(91,141,239,0.12)', color: '#2A57C6', borderColor: 'rgba(91,141,239,0.28)' }}>✓ 质量自检通过</Tag>
            : <Tag style={{ background: 'rgba(180,35,24,0.10)', color: '#B42318', borderColor: 'rgba(180,35,24,0.22)' }}>机检 {checks.error_count} 处硬伤（发布会被拦截）</Tag>}
        </div>
      )}
    </Card>
  );
};

export default BuildResultsPanel;

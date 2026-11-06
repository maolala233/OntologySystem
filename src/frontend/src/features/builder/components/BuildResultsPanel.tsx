/**
 * 本体构建结果卡（2026-11-05 精简定稿）：
 * 单张浅色卡片与系统品牌风格一致——标题+质量自检标签，正文四格彩色统计
 * （浅色底、彩色图标数字），右上角 查看图谱 / 发布到资产中心（一键直发，
 * 发布后翻转 已发布 vN + 取消发布 + 重新发布）。分类明细表与导出已按需求移除。
 */
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Card, Col, Row, Space, Statistic, Tag, message } from 'antd';
import {
  AppstoreOutlined, CloudUploadOutlined, NodeIndexOutlined,
  PartitionOutlined, SafetyCertificateOutlined, ClusterOutlined,
} from '@ant-design/icons';

import { BuildSummary } from '../../../api/pipeline';
import { publishApi } from '../../../api/governance';

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
  const published = publishedVersionNo != null;

  const doPublish = async () => {
    setPublishing(true);
    try {
      const r = await publishApi.publish(projectId, {});
      message.success(`发布完成：v${r.version_no} 已进入资产中心`);
      onPublishedChange?.();
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

  // 浅色系统计格：品牌蓝/紫/青/橙的浅底，数字用对应深色，与系统配色一致
  const statCards = [
    { title: '类', value: summary.schema.classes_total, icon: <AppstoreOutlined />, bg: '#EEF3FE', color: '#3D6FE0' },
    { title: '关系', value: summary.schema.object_properties_total, icon: <PartitionOutlined />, bg: '#F3EEFD', color: '#8B5CD6' },
    { title: '实例', value: summary.instances.total, icon: <ClusterOutlined />, bg: '#E8F7F5', color: '#189F8F' },
    { title: '公理', value: summary.axioms.total, icon: <SafetyCertificateOutlined />, bg: '#FDF3E7', color: '#D9822B' },
  ];

  return (
    <Card size="small"
          title={<span className="text-base">本体构建结果</span>}
          extra={(
            <Space wrap>
              {published && <Tag color="purple">已发布 v{publishedVersionNo}</Tag>}
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
          <Col span={6} key={st.title}>
            <div className="rounded-lg px-4 py-3" style={{ background: st.bg }}>
              <Statistic
                title={<span className="text-xs text-gray-500">{st.title}</span>}
                value={st.value}
                prefix={<span style={{ color: st.color, marginRight: 6, fontSize: 17 }}>{st.icon}</span>}
                valueStyle={{ color: st.color, fontSize: 26, fontWeight: 600 }}
              />
            </div>
          </Col>
        ))}
      </Row>
      {checks && (
        <div className="mt-2">
          {checks.error_count === 0
            ? <Tag color="green">✓ 质量自检通过</Tag>
            : <Tag color="red">机检 {checks.error_count} 处硬伤（发布会被拦截）</Tag>}
        </div>
      )}
    </Card>
  );
};

export default BuildResultsPanel;

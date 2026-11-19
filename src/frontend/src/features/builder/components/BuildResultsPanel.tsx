/**
 * 本体构建结果卡（2026-11-05 精简定稿；2026-11-17 按浅色冷调白卡统一视觉）：
 * 单张浅色卡片与系统品牌风格一致——标题 + 右上角操作，正文四格统计
 * （发丝线分列、标签前荧光圆点、等宽彩色数字），下方一行质量自检状态。
 * 右上角 查看图谱 / 发布到资产中心（一键直发，发布后翻转 已发布 vN + 取消发布 + 重新发布）。
 * 分类明细表与导出已按需求移除。
 */
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { message } from '../../../shared/feedback';
import { Button, Card, Space, Tag } from 'antd';
import {
  AppstoreOutlined, CloudUploadOutlined, NodeIndexOutlined,
  PartitionOutlined, SafetyCertificateOutlined, ClusterOutlined,
} from '@ant-design/icons';

import { BuildSummary } from '../../../api/pipeline';
import { publishApi } from '../../../api/governance';
import { publishWithDomain } from './publishFlow';
import DecoConstellation from '../../../components/DecoConstellation';

interface Props {
  projectId: number;
  summary: BuildSummary;
  checks?: BuildSummary['checks'];
  /** 已发布时的版本号（展示"已发布 vN"标签） */
  publishedVersionNo?: number | null;
  /** 项目是否已有知识域：false 时直接弹选择器，不再发一次注定 400 的探测请求 */
  hasDomain?: boolean;
  onPublishedChange?: () => void;
}

const BuildResultsPanel: React.FC<Props> = ({ projectId, summary, checks, publishedVersionNo, hasDomain, onPublishedChange }) => {
  const navigate = useNavigate();
  const [publishing, setPublishing] = useState(false);
  // 发布状态以后端 is_published 为准；版本号仅取自 publication 快照供展示
  // （单看版本号会把"刚入库的 schema/full 快照"误判成已发布——2026-11-06 实测修复）
  const published = !!summary.is_published && publishedVersionNo != null;

  const doPublish = async () => {
    setPublishing(true);
    try {
      // 无知识域时弹选择器带域发布（2026-11-07：选域从构建期移到发布期）
      const r = await publishWithDomain(projectId, { hasDomain });
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

  // 统计格（2026-11-17）：四项指标不再各来一套渐变卡，改成一条发丝线分列的指标带——
  // 与资产详情 / 图谱页左栏的「类 / 实例 / 关系 / 属性」同一套：标签前荧光圆点、
  // 等宽彩色数字，四项共用一个浅面，不再在卡里再切四块小卡。
  // 右角给一枚同色淡底图标块（与「我的项目」统计卡同一件装饰），把右侧那片空白填掉
  const statCards = [
    { title: '类', value: summary.schema.classes_total, c: '#6D3FD8', t: 'rgba(139,92,246,0.16)', icon: <AppstoreOutlined /> },
    { title: '关系', value: summary.relation_stats?.total_edges ?? summary.relations_total, c: '#1B87AD', t: 'rgba(27,135,173,0.14)', icon: <PartitionOutlined /> },
    { title: '实例', value: summary.instances.total, c: '#2F62D8', t: 'rgba(91,141,239,0.16)', icon: <ClusterOutlined /> },
    { title: '公理', value: summary.axioms.total, c: '#B8790F', t: 'rgba(242,185,80,0.24)', icon: <SafetyCertificateOutlined /> },
  ];
  const checkOk = !checks || checks.error_count === 0;

  return (
    <Card size="small"
          // 段标题字号交给外层统一控制（2026-11-16）：这里原来写死 text-base(16px)，
          // 比另外两段（构建 / 文档）的 14px 大一号；去掉后跟随卡头样式，
          // 在其他页面（没有本页的 .overview-stack 覆盖）仍是 antd 默认的 16px，不受影响
          title={<span>本体构建结果</span>}
          extra={(
            <Space wrap>
              {published && (
                <Tag style={{
                  background: 'rgba(139,92,246,0.14)', color: '#6D3FD8',
                  borderColor: 'rgba(139,92,246,0.30)', marginInlineEnd: 0,
                }}>
                  {publishedVersionNo != null ? `已发布 v${publishedVersionNo}` : '已发布'}
                </Tag>
              )}
              <Button size="small" type="primary" ghost icon={<NodeIndexOutlined />}
                      onClick={() => navigate(`/projects/${projectId}/result?tab=graph`)}>
                查看图谱
              </Button>
              {published && (
                // 取消发布是破坏性操作，但它不是这一屏的主操作：用中性描边，
                // hover 才露出冷调红，避免和右侧「重新发布」抢注意力
                <Button size="small" className="br-unpublish" onClick={doUnpublish}>取消发布</Button>
              )}
              <Button size="small" type="primary" icon={<CloudUploadOutlined />}
                      loading={publishing} onClick={doPublish}>
                {published ? '重新发布' : '发布到资产中心'}
              </Button>
            </Space>
          )}>
      {/* 结构指标带（2026-11-17）：从四块白格子换成一条「冷蓝纸 + 柔光 + 节点线稿」的指标带，
          竖向发丝线分列；四格透明，让底纹透出来，不再是一排白底 */}
      <div
        className="relative overflow-hidden rounded-[12px] border border-[rgba(91,141,239,0.18)]"
        style={{
          background:
            'radial-gradient(520px 220px at 100% 0%, rgba(91,141,239,0.16), transparent 72%),'
            + 'radial-gradient(420px 240px at 0% 100%, rgba(139,92,246,0.09), transparent 74%),'
            + 'linear-gradient(150deg, #FBFDFF 0%, #F1F7FF 58%, #EDF3FE 100%)',
        }}
      >
        <DecoConstellation id="br-stats"
                           className="pointer-events-none absolute -bottom-5 -right-6 h-[170px] w-[440px] opacity-70"
                           style={{ transform: 'scaleY(-1)' }} />
        <div className="br-stat-grid relative">
          {statCards.map((st) => (
            <div key={st.title} className="flex items-center justify-between gap-2 px-3.5 py-3">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-[11.5px] text-[#8A94AB]">
                  <span className="h-[6px] w-[6px] flex-none rounded-full"
                        style={{ background: st.c, boxShadow: `0 0 0 3px ${st.t}` }} />
                  {st.title}
                </div>
                <div className="tk-mono mt-1.5 text-[23px] font-semibold leading-none" style={{ color: st.c }}>
                  {Number(st.value ?? 0).toLocaleString('zh-CN')}
                </div>
              </div>
              {/* 右角图标块：同色淡底 + 深一档图标色（与「我的项目」统计卡同一件装饰） */}
              <span className="flex h-8 w-8 flex-none items-center justify-center rounded-[9px] text-[15px]"
                    style={{ background: st.t, color: st.c }}>
                {st.icon}
              </span>
            </div>
          ))}
        </div>
      </div>
      {checks && (
        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px]">
          <span className="flex items-center gap-1.5"
                style={{ color: checkOk ? '#1B87AD' : '#B42318' }}>
            <span className="h-[7px] w-[7px] flex-none rounded-full"
                  style={{
                    background: checkOk ? '#34C3A4' : '#F2685C',
                    boxShadow: checkOk
                      ? '0 0 6px rgba(52,195,164,0.75)'
                      : '0 0 6px rgba(242,104,92,0.75)',
                  }} />
            {checkOk
              ? '机检通过'
              : `机检 ${checks.error_count} 处问题（发布会被拦截）`}
          </span>
        </div>
      )}
      <style>{`
        /* 取消发布：中性描边 + 灰字（压过页面的次要按钮配色），hover 才转冷调红（2026-11-17） */
        .br-unpublish.ant-btn:not(:disabled) { color: #8A94AB !important; }
        .br-unpublish.ant-btn:not(:disabled):hover {
            color: #B42318 !important;
            border-color: rgba(180, 35, 24, 0.35) !important;
            background: rgba(180, 35, 24, 0.04) !important;
        }
        /* 指标带分列（2026-11-17）：四格透明，靠竖/横发丝线分列（不做四块小卡）。
           窄屏两列、宽屏四列：竖线用 ::before（每行首格不画），横线用 ::after（第二行起） */
        .br-stat-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .br-stat-grid > div { position: relative; }
        .br-stat-grid > div::before {
            content: ''; position: absolute; left: 0; top: 12px; bottom: 12px;
            width: 1px; background: rgba(91, 141, 239, 0.14);
        }
        .br-stat-grid > div:nth-child(2n + 1)::before { display: none; }
        .br-stat-grid > div:nth-child(n + 3)::after {
            content: ''; position: absolute; left: 14px; right: 14px; top: 0; height: 1px;
            background: rgba(91, 141, 239, 0.14);
        }
        @media (min-width: 768px) {
            .br-stat-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
            .br-stat-grid > div::before { display: block; }
            .br-stat-grid > div:first-child::before { display: none; }
            .br-stat-grid > div::after { display: none !important; }
        }
      `}</style>
    </Card>
  );
};

export default BuildResultsPanel;

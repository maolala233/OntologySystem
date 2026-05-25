import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { Typography } from 'antd';

const { Text } = Typography;

const Neo4jNode = ({ data, selected, isConnectable, dragging }: NodeProps) => {
    // 判断是否为 Action Type 节点（通过 raw_id 或 id 前缀识别）
    const rawId = data.raw_id || '';
    const nodeId = data.id || '';
    const isActionType = rawId.startsWith('AT_') || nodeId.startsWith('AT_');

    // 颜色映射
    let bgColor: string;
    let borderColor: string;
    let fontSize: number;
    let size: number;

    if (isActionType) {
        // Action Type 节点：灰黑色样式
        bgColor = '#f5f5f5';
        borderColor = selected ? '#b0b0b0' : '#d0d0d0';
        fontSize = 11;
        size = 70;
    } else if (data.type === 'owl:Class') {
        // Object Type 节点：蓝色
        bgColor = '#68bdf6';
        borderColor = selected ? '#bce0fd' : 'white';
        fontSize = 12;
        size = 80;
    } else if (data.type === 'owl:NamedIndividual') {
        // 实例节点：橙色
        bgColor = '#f79767';
        borderColor = selected ? '#fcd5b8' : 'white';
        fontSize = 10;
        size = 50;
    } else {
        // 其他节点：紫色
        bgColor = '#c990c0';
        borderColor = selected ? '#e0c0db' : 'white';
        fontSize = 10;
        size = 50;
    }

    // Action Type 节点文字颜色
    const textColor = isActionType ? '#666666' : '#fff';
    const opacity = isActionType ? 0.85 : 1;

    return (
        <div style={{
            width: size,
            height: size,
            borderRadius: '50%',
            backgroundColor: bgColor,
            border: `2px solid ${borderColor}`,
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            boxShadow: isActionType ? '0 2px 4px rgba(0,0,0,0.08)' : '0 3px 6px rgba(0,0,0,0.16)',
            cursor: 'pointer',
            pointerEvents: 'auto',
            userSelect: 'none',
            WebkitUserSelect: 'none',
            MozUserSelect: 'none',
            msUserSelect: 'none',
            transition: 'all 0.3s ease',
            position: 'relative',
            opacity,
        }}>
            <Handle type="target" position={Position.Top} isConnectable={isConnectable} style={{ opacity: 0 }} />

            <div style={{
                textAlign: 'center',
                color: textColor,
                fontSize: fontSize,
                padding: 4,
                overflow: 'hidden',
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                lineHeight: 1.2
            }}>
                <Text strong style={{ color: textColor }}>
                    {data.currentLang === 'en' ? (data.labelEn || data.label) : data.label}
                </Text>
            </div>

            <Handle type="source" position={Position.Bottom} isConnectable={isConnectable} style={{ opacity: 0 }} />
        </div>
    );
};

export default memo(Neo4jNode);
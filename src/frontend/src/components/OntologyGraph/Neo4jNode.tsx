import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { Typography } from 'antd';

const { Text } = Typography;

const Neo4jNode = ({ data, selected, isConnectable, dragging }: NodeProps) => {
    // 颜色映射
    const bgColor = data.type === 'owl:Class' ? '#68bdf6' :
                   (data.type === 'owl:NamedIndividual' ? '#f79767' : '#c990c0');

    // 大小映射
    const size = data.type === 'owl:Class' ? 80 : 50;

    console.log('Rendering Neo4jNode:', data.label, 'Selected:', selected); // 添加调试日志

    return (
        <div style={{
            width: size,
            height: size,
            borderRadius: '50%',
            backgroundColor: bgColor,
            border: selected ? '4px solid #bce0fd' : '2px solid white',
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            boxShadow: '0 3px 6px rgba(0,0,0,0.16)',
            cursor: 'pointer', // 改为 pointer 便于识别
            pointerEvents: 'auto',
            userSelect: 'none',
            WebkitUserSelect: 'none',
            MozUserSelect: 'none',
            msUserSelect: 'none',
            transition: 'all 0.3s ease',
            position: 'relative'
        }}>
            <Handle type="target" position={Position.Top} isConnectable={isConnectable} style={{ opacity: 0 }} />

            <div style={{
                textAlign: 'center',
                color: '#fff',
                fontSize: data.type === 'owl:Class' ? 12 : 10,
                padding: 4,
                overflow: 'hidden',
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                lineHeight: 1.2
            }}>
                <Text strong style={{ color: 'white' }}>
                    {data.currentLang === 'en' ? (data.labelEn || data.label) : data.label}
                </Text>
            </div>

            <Handle type="source" position={Position.Bottom} isConnectable={isConnectable} style={{ opacity: 0 }} />
        </div>
    );
};

export default memo(Neo4jNode);
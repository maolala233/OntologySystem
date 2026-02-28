import React from 'react';
import { EdgeProps, getBezierPath } from 'reactflow';

/**
 * 自定义直线边组件 - 使用直线连接节点
 */
export const StraightEdge: React.FC<EdgeProps> = ({
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    style = {},
    data,
    markerEnd,
}) => {
    // 计算直线路径
    const edgePath = `M${sourceX},${sourceY} L${targetX},${targetY}`;
    
    // 计算标签位置（中点）
    const labelX = (sourceX + targetX) / 2;
    const labelY = (sourceY + targetY) / 2;

    return (
        <>
            <path
                id={id}
                style={style}
                className="react-flow__edge-path"
                d={edgePath}
                markerEnd={markerEnd}
            />
            {data?.label && (
                <g>
                    <rect
                        x={labelX - 30}
                        y={labelY - 10}
                        width={60}
                        height={20}
                        fill="white"
                        fillOpacity={0.8}
                        rx={4}
                    />
                    <text
                        x={labelX}
                        y={labelY + 4}
                        textAnchor="middle"
                        fontSize={11}
                        fill="#666"
                    >
                        {data.label}
                    </text>
                </g>
            )}
        </>
    );
};

/**
 * 带箭头的直线边组件
 */
export const StraightArrowEdge: React.FC<EdgeProps> = ({
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    style = {},
    data,
    markerEnd,
}) => {
    // 计算直线路径
    const edgePath = `M${sourceX},${sourceY} L${targetX},${targetY}`;
    
    // 计算标签位置（中点）
    const labelX = (sourceX + targetX) / 2;
    const labelY = (sourceY + targetY) / 2;

    const defaultStyle: React.CSSProperties = {
        stroke: '#b1b1b7',
        strokeWidth: 1.5,
        ...style,
    };

    return (
        <>
            <path
                id={id}
                style={defaultStyle}
                className="react-flow__edge-path"
                d={edgePath}
                markerEnd={markerEnd}
            />
            {data?.label && (
                <g>
                    <rect
                        x={labelX - 40}
                        y={labelY - 10}
                        width={80}
                        height={20}
                        fill="white"
                        fillOpacity={0.9}
                        rx={4}
                        stroke="#e0e0e0"
                        strokeWidth={0.5}
                    />
                    <text
                        x={labelX}
                        y={labelY + 4}
                        textAnchor="middle"
                        fontSize={10}
                        fill="#333"
                        fontWeight={500}
                    >
                        {data.label}
                    </text>
                </g>
            )}
        </>
    );
};
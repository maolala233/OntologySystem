import { useState } from 'react';
import { Modal, Radio, Space, Typography, message } from 'antd';
import { ExportOutlined } from '@ant-design/icons';
import { projectsApi, EXPORT_FORMAT_EXT, type ExportFormatId } from '../../../api/projects';

const { Text } = Typography;

interface FormatOption {
    id: ExportFormatId;
    label: string;
    desc: string;
}

const RDF_FORMATS: FormatOption[] = [
    { id: 'turtle', label: 'Turtle (.ttl)', desc: '最常用的 RDF 文本格式，推荐' },
    { id: 'ntriples', label: 'N-Triples (.nt)', desc: '逐行三元组，适合流式处理' },
    { id: 'rdfxml', label: 'RDF/XML (.rdf)', desc: 'XML 序列化，传统 RDF 工具链' },
    { id: 'jsonld', label: 'JSON-LD (.jsonld)', desc: 'JSON 形式的链接数据' },
    { id: 'trig', label: 'TriG (.trig)', desc: '支持命名图的 Turtle 扩展' },
    { id: 'owl', label: 'OWL/XML (.owl)', desc: 'OWL 本体标准 XML 格式' },
];

const JSON_FORMATS: FormatOption[] = [
    { id: 'json', label: '平台 JSON (.json)', desc: '平台专有格式，可直接导回平台画布' },
];

interface ExportDialogProps {
    open: boolean;
    onClose: () => void;
    projectId: number;
    /** 弹窗标题，默认"导出图谱" */
    title?: string;
}

/** 统一导出弹窗：骨架编辑 / 实例探索 / 资产中心共用 */
export default function ExportDialog({ open, onClose, projectId, title = '导出图谱' }: ExportDialogProps) {
    const [format, setFormat] = useState<ExportFormatId>('turtle');
    const [downloading, setDownloading] = useState(false);

    const handleExport = async () => {
        setDownloading(true);
        try {
            await projectsApi.exportProject(projectId, format);
            message.success(`已开始下载 .${EXPORT_FORMAT_EXT[format]} 文件`);
            onClose();
        } catch (e: any) {
            const detail = e?.response?.data;
            let msg = '导出失败';
            if (detail instanceof Blob) {
                try {
                    const text = JSON.parse(await detail.text());
                    msg = text.detail || msg;
                } catch { /* 保留默认提示 */ }
            } else if (typeof detail === 'string') {
                msg = detail;
            } else if (detail?.detail) {
                msg = detail.detail;
            }
            message.error(msg);
        } finally {
            setDownloading(false);
        }
    };

    const renderGroup = (label: string, options: FormatOption[]) => (
        <div className="mb-3">
            <Text type="secondary" className="!text-xs">{label}</Text>
            <Radio.Group
                value={format}
                onChange={(e) => setFormat(e.target.value)}
                className="!mt-1 !w-full"
            >
                <Space direction="vertical" className="!w-full">
                    {options.map((opt) => (
                        <Radio key={opt.id} value={opt.id} className="!w-full">
                            <span className="font-medium">{opt.label}</span>
                            <Text type="secondary" className="!ml-2 !text-xs">{opt.desc}</Text>
                        </Radio>
                    ))}
                </Space>
            </Radio.Group>
        </div>
    );

    return (
        <Modal
            title={<><ExportOutlined /> {title}</>}
            open={open}
            onCancel={onClose}
            onOk={handleExport}
            okText="导出"
            cancelText="取消"
            confirmLoading={downloading}
            okButtonProps={{ disabled: downloading }}
            width={480}
            destroyOnClose
        >
            <div className="pt-1">
                {renderGroup('RDF 格式（semantica 序列化，可被 Protégé / rdflib 等标准工具读取）', RDF_FORMATS)}
                {renderGroup('JSON', JSON_FORMATS)}
                <Text type="secondary" className="!text-xs">
                    导出的 RDF 文件可通过"导入本体"重新导入平台；平台 JSON 保留画布完整结构。
                </Text>
            </div>
        </Modal>
    );
}

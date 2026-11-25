import { useState } from 'react';
import { message } from '../../../shared/feedback';
import { Modal, Radio } from 'antd';
import { ExportOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { projectsApi, EXPORT_FORMAT_EXT, type ExportFormatId } from '../../../api/projects';

interface FormatOption {
    id: ExportFormatId;
    label: string;
    desc: string;
}

const RDF_FORMATS: FormatOption[] = [
    { id: 'turtle', label: 'Turtle', desc: '最常用的 RDF 文本格式，推荐' },
    { id: 'ntriples', label: 'N-Triples', desc: '逐行三元组，适合流式处理' },
    { id: 'rdfxml', label: 'RDF/XML', desc: 'XML 序列化，传统 RDF 工具链' },
    { id: 'jsonld', label: 'JSON-LD', desc: 'JSON 形式的链接数据' },
    { id: 'trig', label: 'TriG', desc: '支持命名图的 Turtle 扩展' },
    { id: 'owl', label: 'OWL/XML', desc: 'OWL 本体标准 XML 格式' },
];

const JSON_FORMATS: FormatOption[] = [
    { id: 'json', label: '平台 JSON', desc: '平台专有格式，可直接导回平台画布' },
];

interface ExportDialogProps {
    open: boolean;
    onClose: () => void;
    projectId: number;
    /** 弹窗标题，默认"导出图谱" */
    title?: string;
}

/**
 * 统一导出弹窗：骨架编辑 / 实例探索 / 资产中心共用。
 * 2026-11-18 版式：七种格式不再是一长串素单选，改成「分组 + 格式卡片」——
 * 每项带扩展名徽标、名称、一句说明，选中整卡高亮；底部说明收进浅色提示条。
 */
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

    const renderGroup = (label: string, hint: string, options: FormatOption[]) => (
        <div className="tk-export-group">
            <div className="tk-export-group-head">
                <span className="tk-export-group-title">
                    <span className="tk-export-dot" aria-hidden />
                    {label}
                </span>
                <span className="tk-export-group-hint">{hint}</span>
            </div>
            <Radio.Group
                value={format}
                onChange={(e) => setFormat(e.target.value)}
                className="tk-export-radio-group"
            >
                <div className="tk-export-grid">
                    {options.map((opt) => (
                        <Radio key={opt.id} value={opt.id} className="tk-export-option">
                            {/* 单行：名称 + 扩展名徽标在左，说明靠右对齐（2026-11-25）。
                                原来是「名称 / 说明」两行叠在左侧，右侧除了徽标整片空着，
                                两列网格下每张卡都像有两行没写满 */}
                            <span className="tk-export-option-body">
                                <span className="tk-export-option-name">{opt.label}</span>
                                <span className="tk-export-option-ext">{EXPORT_FORMAT_EXT[opt.id]}</span>
                                <span className="tk-export-option-desc">{opt.desc}</span>
                            </span>
                        </Radio>
                    ))}
                </div>
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
            width={560}
            destroyOnHidden
        >
            <style>{`
                .tk-export-group { margin-bottom: 12px; }
                .tk-export-group:last-of-type { margin-bottom: 0; }
                .tk-export-group-head {
                    display: flex; flex-wrap: wrap; align-items: baseline;
                    gap: 2px 10px; margin-bottom: 6px;
                }
                .tk-export-group-title {
                    display: inline-flex; align-items: center; gap: 6px;
                    color: #33415C; font-size: 12.5px; font-weight: 600;
                }
                .tk-export-dot {
                    width: 6px; height: 6px; flex: none; border-radius: 9999px;
                    background: #5B8DEF; box-shadow: 0 0 0 3px rgba(91, 141, 239, 0.14);
                }
                .tk-export-group-hint { color: #A3ADC2; font-size: 11.5px; }
                .tk-export-radio-group { display: block; width: 100%; }
                /* 单行条目（2026-11-25）：由两列卡片改成通栏细行，说明靠右对齐，
                   宽度利用起来，7 个格式也不再是"每张卡左半满、右半空" */
                .tk-export-grid { display: flex; flex-direction: column; gap: 5px; }
                .tk-export-option.ant-radio-wrapper {
                    display: flex; align-items: center; gap: 9px;
                    width: 100%; margin: 0; padding: 6px 10px;
                    border: 1px solid #E4ECF8; border-radius: 9px;
                    background: #FBFDFF;
                    transition: border-color 0.18s ease, background 0.18s ease, box-shadow 0.18s ease;
                }
                .tk-export-option.ant-radio-wrapper:hover {
                    border-color: rgba(91, 141, 239, 0.45);
                    background: #F7FAFF;
                }
                .tk-export-option.ant-radio-wrapper-checked {
                    border-color: rgba(91, 141, 239, 0.55);
                    background: linear-gradient(180deg, rgba(91, 141, 239, 0.10) 0%, rgba(91, 141, 239, 0.05) 100%);
                    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7), 0 6px 14px -12px rgba(91, 141, 239, 0.55);
                }
                .tk-export-option.ant-radio-wrapper > span:last-child { flex: 1; min-width: 0; }
                .tk-export-option .ant-radio { margin: 0; flex: none; }
                .tk-export-option-body { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; }
                .tk-export-option-name {
                    flex: none; color: #1F2A44;
                    font-size: 12.5px; line-height: 18px; font-weight: 500;
                }
                .tk-export-option-desc {
                    margin-left: auto; min-width: 0;
                    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                    color: #8A94AB; font-size: 11.5px; line-height: 17px;
                    text-align: right;
                }
                .tk-export-option-ext {
                    flex: none; margin: 0; padding: 1px 6px; border-radius: 6px;
                    background: rgba(91, 141, 239, 0.10); color: #2A57C6;
                    font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
                    font-size: 10.5px; line-height: 16px; letter-spacing: 0.01em;
                }
                .tk-export-option.ant-radio-wrapper-checked .tk-export-option-name {
                    color: #2A57C6; font-weight: 600;
                }
                .tk-export-option.ant-radio-wrapper-checked .tk-export-option-ext {
                    background: rgba(91, 141, 239, 0.18);
                }
                .tk-export-note {
                    display: flex; align-items: flex-start; gap: 8px;
                    margin-top: 10px; padding: 7px 10px;
                    border: 1px solid rgba(91, 141, 239, 0.14); border-radius: 10px;
                    background: rgba(91, 141, 239, 0.06);
                    color: #8A94AB; font-size: 11.5px; line-height: 18px;
                }
                .tk-export-note-icon { margin-top: 2px; flex: none; color: #5B8DEF; font-size: 12px; }
            `}</style>
            <div>
                {renderGroup('RDF 格式', '可被 Protégé / rdflib 等标准工具读取', RDF_FORMATS)}
                {renderGroup('平台格式', '保留画布完整结构，可直接导回', JSON_FORMATS)}
                <div className="tk-export-note">
                    <InfoCircleOutlined className="tk-export-note-icon" />
                    <span>导出的 RDF 文件可通过「导入本体」重新导入平台。</span>
                </div>
            </div>
        </Modal>
    );
}

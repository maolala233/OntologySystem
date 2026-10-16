// src/components/Copilot/ClarifyOptions.tsx - 澄清选项（O5-S5 F16，用户反馈①②）
// 交互改版：点击选项只是"选中"（高亮），点「确认」才发送，防误触误发；
// 「其他」点开内嵌输入框，用户自己描述（回车或点发送提交）。
import { useState } from 'react';
import { Button, Input } from 'antd';
import { CheckOutlined, SendOutlined } from '@ant-design/icons';

interface Props {
    options: string[];
    busy: boolean;
    onSend: (text: string) => void;
}

const OTHER = '其他';

export default function ClarifyOptions({ options, busy, onSend }: Props) {
    const [selected, setSelected] = useState<string | null>(null);
    const [otherMode, setOtherMode] = useState(false);
    const [otherText, setOtherText] = useState('');

    const submitOther = () => {
        const t = otherText.trim();
        if (t && !busy) onSend(t);
    };

    return (
        <div className="mt-2 space-y-1.5">
            <div className="flex flex-wrap gap-1.5">
                {options.map(opt => opt === OTHER ? (
                    <Button key={opt} size="small" disabled={busy}
                            type={otherMode ? 'primary' : 'default'}
                            ghost={otherMode}
                            onClick={() => { setOtherMode(v => !v); setSelected(null); }}>
                        其他（自己输入）
                    </Button>
                ) : (
                    <Button key={opt} size="small" disabled={busy}
                            type={selected === opt ? 'primary' : 'default'}
                            onClick={() => { setSelected(opt); setOtherMode(false); }}>
                        {opt}
                    </Button>
                ))}
            </div>
            {otherMode && (
                <div className="flex items-center gap-1.5">
                    <Input size="small" autoFocus value={otherText}
                           disabled={busy}
                           placeholder="描述你想做的操作…"
                           onChange={e => setOtherText(e.target.value)}
                           onPressEnter={submitOther} />
                    <Button size="small" type="primary" icon={<SendOutlined />}
                            disabled={busy || !otherText.trim()} onClick={submitOther} />
                </div>
            )}
            {!otherMode && selected && (
                <div className="flex items-center gap-2">
                    <Button size="small" type="primary" icon={<CheckOutlined />}
                            disabled={busy}
                            onClick={() => selected && onSend(selected)}>
                        确认{selected ? `：${selected.length > 18 ? selected.slice(0, 18) + '…' : selected}` : ''}
                    </Button>
                    <span className="text-[10px] text-gray-400">已选中，点确认才执行</span>
                </div>
            )}
        </div>
    );
}

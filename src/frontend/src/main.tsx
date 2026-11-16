import React from 'react'
import ReactDOM from 'react-dom/client'
import dayjs from 'dayjs'
// 日期面板的中英文由 dayjs 的 locale 决定，不是 antd 的 ConfigProvider：
// antd 的 zh_CN 只提供「今天/此刻/确定」这类按钮文案，月份名与星期缩写走
// dayjs 的 shortWeekDays/months，不设这里就会一直是 Nov / Su Mo Tu（2026-11-16）
import 'dayjs/locale/zh-cn'
import App from './App.tsx'
import './index.css'

dayjs.locale('zh-cn')   // 周一为一周起始，符合国内习惯

ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
        <App />
    </React.StrictMode>,
)

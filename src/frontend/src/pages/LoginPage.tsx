import React, { useState } from 'react';
import { message } from '../shared/feedback';
import { Form, Input, Button } from 'antd';
import {
    UserOutlined, LockOutlined, ArrowRightOutlined, SafetyCertificateOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { authAPI } from '../api/auth';
import { useAuthStore } from '../shared/auth/authStore';
import BrandMark from '../components/BrandMark';
import DecoConstellation from '../components/DecoConstellation';
import canvasBg from '../assets/app-canvas-bg.svg';

const FEATURES = [
    { index: '01', title: 'AI 骨架抽取', desc: '从文档中抽取类、关系与属性，生成可审阅的本体骨架' },
    { index: '02', title: '图谱与实例探索', desc: '画布编辑、实例抽取与邻域分析，结构关系一屏看清' },
    { index: '03', title: '发布与资产治理', desc: '审核、版本、质量门控与资产中心，让知识资产持续运营' },
];

const CAPABILITIES = {
    login: ['AI 抽取', '图谱探索', '资产治理'],
    register: ['创建工作台', '项目隔离', '安全发布'],
} as const;

const LoginPage: React.FC = () => {
    const navigate = useNavigate();
    const setTokens = useAuthStore((s) => s.setTokens);
    const hydrate = useAuthStore((s) => s.hydrate);
    const [loading, setLoading] = useState(false);
    const [activeTab, setActiveTab] = useState<'login' | 'register'>('login');

    const handleLogin = async (values: any) => {
        setLoading(true);
        try {
            const response = await authAPI.login({
                username: values.username,
                password: values.password,
            });

            setTokens(response.access_token, response.refresh_token || '');
            await hydrate(); // M1：拉取 /auth/me（role + modules[]），驱动菜单与路由守卫

            message.success('登录成功！');
            navigate('/');
        } catch (error: any) {
            message.error(error.response?.data?.detail || '登录失败，请检查用户名和密码');
        } finally {
            setLoading(false);
        }
    };

    const handleRegister = async (values: any) => {
        setLoading(true);
        try {
            const response = await authAPI.register({
                username: values.username,
                password: values.password,
            });

            setTokens(response.access_token, response.refresh_token || '');
            await hydrate();

            message.success('注册成功！');
            navigate('/');
        } catch (error: any) {
            message.error(error.response?.data?.detail || '注册失败，请稍后重试');
        } finally {
            setLoading(false);
        }
    };

    const renderForm = (mode: 'login' | 'register') => (
        <Form
            key={mode}
            onFinish={mode === 'login' ? handleLogin : handleRegister}
            layout="vertical"
            size="middle"
            requiredMark={false}
            className="login-form"
        >
            <Form.Item
                name="username"
                label="用户名"
                rules={[{ required: true, message: '请输入用户名' }]}
            >
                <Input
                    prefix={<UserOutlined className="login-field-icon" />}
                    placeholder="请输入用户名"
                    autoComplete="username"
                />
            </Form.Item>

            {mode === 'login' && (
                <Form.Item
                    name="password"
                    label="密码"
                    rules={[{ required: true, message: '请输入密码' }]}
                >
                    <Input.Password
                        prefix={<LockOutlined className="login-field-icon" />}
                        placeholder="请输入密码"
                        autoComplete="current-password"
                    />
                </Form.Item>
            )}

            {mode === 'register' && (
                <div className="login-field-row">
                    <Form.Item
                        name="password"
                        label="密码"
                        rules={[
                            { required: true, message: '请输入密码' },
                            { min: 6, message: '密码至少 6 位' },
                        ]}
                    >
                        <Input.Password
                            prefix={<LockOutlined className="login-field-icon" />}
                            placeholder="至少 6 位"
                            autoComplete="new-password"
                        />
                    </Form.Item>

                    <Form.Item
                        name="confirmPassword"
                        label="确认密码"
                        dependencies={['password']}
                        rules={[
                            { required: true, message: '请确认密码' },
                            ({ getFieldValue }) => ({
                                validator(_, value) {
                                    if (!value || getFieldValue('password') === value) {
                                        return Promise.resolve();
                                    }
                                    return Promise.reject(new Error('两次输入密码不一致'));
                                },
                            }),
                        ]}
                    >
                        <Input.Password
                            prefix={<LockOutlined className="login-field-icon" />}
                            placeholder="再次输入"
                            autoComplete="new-password"
                        />
                    </Form.Item>
                </div>
            )}

            <Button
                type="primary"
                htmlType="submit"
                loading={loading}
                block
                className="login-submit"
                icon={<ArrowRightOutlined />}
                iconPosition="end"
            >
                {mode === 'login' ? '进入工作台' : '创建账号'}
            </Button>
        </Form>
    );

    return (
        <div className="login-shell">
            <style>{`
                .login-shell {
                    position: relative;
                    height: 100vh;
                    overflow-y: auto;
                    overflow-x: hidden;
                    background-color: #EAF1FB;
                }
                .login-bg {
                    position: fixed; inset: 0;
                    background-size: cover;
                    background-position: center;
                    background-repeat: no-repeat;
                    opacity: 0.95;
                }
                .login-bg::after {
                    content: '';
                    position: absolute; inset: 0;
                    background:
                        radial-gradient(760px 420px at 12% 18%, rgba(91,141,239,0.12), transparent 70%),
                        radial-gradient(640px 380px at 88% 82%, rgba(139,92,246,0.08), transparent 72%),
                        linear-gradient(120deg, rgba(255,255,255,0.22) 0%, rgba(255,255,255,0.04) 100%);
                }
                .login-deco {
                    position: fixed; left: -40px; bottom: -18px;
                    width: 620px; height: 240px; opacity: 0.55;
                    pointer-events: none;
                }
                .login-stage {
                    position: relative; z-index: 10;
                    min-height: 100vh;
                    width: 100%;
                    max-width: 1320px;
                    margin: 0 auto;
                    padding: clamp(28px, 6vh, 72px) clamp(24px, 5vw, 64px);
                    display: grid;
                    grid-template-columns: minmax(0, 1.12fr) minmax(0, 0.88fr);
                    align-items: center;
                    gap: clamp(56px, 5vw, 108px);
                }
                .login-hero { min-width: 0; }
                .login-eyebrow {
                    display: inline-flex; align-items: center; gap: 8px;
                    color: #52607A; font-size: 10.5px; letter-spacing: 0.22em;
                    font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
                    text-transform: uppercase;
                }
                .login-eyebrow-dot {
                    width: 6px; height: 6px; border-radius: 9999px; flex: none;
                    background: #5B8DEF;
                    box-shadow: 0 0 0 4px rgba(91,141,239,0.14), 0 0 14px rgba(91,141,239,0.55);
                }
                .login-hero-title {
                    margin-top: 20px;
                    color: #0F172A;
                    font-size: clamp(36px, 4vw, 52px);
                    line-height: 1.16;
                    font-weight: 700;
                    letter-spacing: -0.02em;
                }
                .login-hero-title > span {
                    background: linear-gradient(95deg, #2A57C6 0%, #6F63E8 52%, #8B5CF6 100%);
                    -webkit-background-clip: text;
                    background-clip: text;
                    color: transparent;
                }
                .login-hero-sub {
                    max-width: 520px;
                    margin-top: 18px;
                    color: #52607A;
                    font-size: 14px;
                    line-height: 1.85;
                }
                .login-feature-list { margin-top: 34px; max-width: 540px; }
                .login-feature {
                    display: flex; align-items: flex-start; gap: 14px;
                    padding: 14px 0;
                    border-top: 1px solid rgba(91,141,239,0.12);
                }
                .login-feature-index {
                    flex: none;
                    display: inline-flex; align-items: center; justify-content: center;
                    min-width: 34px; height: 24px;
                    border-radius: 7px;
                    border: 1px solid rgba(91,141,239,0.20);
                    background: rgba(91,141,239,0.08);
                    color: #2A57C6;
                    font-size: 10.5px; letter-spacing: 0.04em;
                }
                .login-feature-title {
                    color: #1F2A44; font-size: 13.5px; font-weight: 600;
                }
                .login-feature-desc {
                    margin-top: 3px; color: #8A94AB; font-size: 12px; line-height: 1.65;
                }
                .login-hero-meta {
                    margin-top: 26px;
                    color: #A3ADC2; font-size: 10px; letter-spacing: 0.20em;
                    font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
                }
                .login-panel { width: 100%; max-width: 470px; margin-left: auto; }
                .login-card {
                    position: relative;
                    display: flex;
                    flex-direction: column;
                    padding: 20px 24px 16px;
                    border-radius: 20px;
                    overflow: hidden;
                    border: 1px solid rgba(91,141,239,0.24);
                    background:
                        radial-gradient(320px 200px at 100% 0%, rgba(91,141,239,0.10), transparent 72%),
                        linear-gradient(160deg, #F0F7FF 0%, #E3EEFD 58%, #DAE8FB 100%);
                    box-shadow:
                        inset 0 1px 0 rgba(255,255,255,0.90),
                        0 34px 80px -40px rgba(15,23,42,0.42),
                        0 8px 24px -18px rgba(91,141,239,0.38);
                    -webkit-backdrop-filter: saturate(150%) blur(14px);
                    backdrop-filter: saturate(150%) blur(14px);
                }
                .login-card > *:not(.login-card-deco) {
                    position: relative; z-index: 1;
                }
                .login-card-deco {
                    position: absolute; right: -46px; bottom: -26px;
                    width: 320px; height: 120px;
                    opacity: 0.42; pointer-events: none; z-index: 0;
                }
                .login-card-top {
                    display: flex; align-items: center; justify-content: space-between; gap: 12px;
                }
                .login-brand { display: flex; align-items: center; gap: 10px; min-width: 0; }
                .login-mark {
                    width: 40px; height: 40px; flex: none;
                    display: inline-flex; align-items: center; justify-content: center;
                    border-radius: 11px;
                    background: linear-gradient(135deg, #5B8DEF 0%, #8B5CF6 100%);
                    box-shadow: 0 10px 24px -12px rgba(91,141,239,0.80), inset 0 1px 0 rgba(255,255,255,0.30);
                }
                .login-brand-text { min-width: 0; }
                .login-brand-eyebrow {
                    display: block; color: #8A94AB; font-size: 9px; letter-spacing: 0.18em;
                    font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
                }
                .login-brand-name {
                    display: block; margin-top: 2px; color: #0F172A; font-size: 13px; font-weight: 600;
                }
                .login-secure {
                    display: inline-flex; align-items: center; gap: 4px; flex: none;
                    padding: 2px 7px; border-radius: 6px;
                    border: 1px solid rgba(91,141,239,0.18);
                    background: rgba(91,141,239,0.07);
                    color: #2A57C6; font-size: 9.5px; letter-spacing: 0.08em;
                    font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
                }
                .login-switch {
                    display: flex; align-items: flex-end; gap: 28px;
                    margin-top: 16px;
                    border-bottom: 1px solid rgba(91,141,239,0.14);
                }
                .login-switch button {
                    position: relative;
                    height: 34px; padding: 0 1px;
                    border: 0; border-radius: 6px 6px 0 0;
                    background: transparent; color: #7A8699;
                    font-size: 15px; font-weight: 500; cursor: pointer;
                    transition: color 0.18s ease;
                }
                .login-switch button:hover { color: #2A57C6; }
                .login-switch button.is-active { color: #0F172A; font-weight: 600; }
                .login-switch button.is-active::after {
                    content: '';
                    position: absolute; left: 0; right: 0; bottom: -1px;
                    height: 2.5px; border-radius: 2px;
                    background: linear-gradient(90deg, #5B8DEF 0%, #8B5CF6 100%);
                    box-shadow: 0 2px 8px -2px rgba(91,141,239,0.75);
                }
                .login-card-title-row {
                    display: flex; align-items: center; gap: 8px;
                    margin-top: 18px;
                }
                .login-title-mark {
                    width: 3px; height: 15px; flex: none; border-radius: 2px;
                    background: linear-gradient(180deg, #5B8DEF 0%, #8B5CF6 100%);
                }
                .login-card-title {
                    color: #0F172A; font-size: 19px; line-height: 26px; font-weight: 700;
                    letter-spacing: -0.01em;
                }
                .login-card-sub {
                    margin-top: 3px;
                    color: #66738F; font-size: 12px; line-height: 17px;
                }
                .login-capabilities {
                    display: flex; flex-wrap: wrap; gap: 6px;
                    margin-top: 10px;
                }
                .login-cap {
                    display: inline-flex; align-items: center; gap: 5px;
                    height: 22px; padding: 0 8px;
                    border-radius: 9999px;
                    border: 1px solid rgba(91,141,239,0.14);
                    background: rgba(91,141,239,0.06);
                    color: #52607A; font-size: 10.5px;
                }
                .login-cap-dot {
                    width: 4px; height: 4px; border-radius: 9999px; flex: none;
                    background: #5B8DEF;
                    box-shadow: 0 0 6px rgba(91,141,239,0.70);
                }
                .login-form { margin-top: 14px; }
                /* 字段外边距 20px 就是报错带：报错占据这段留白，
                   margin-offset 同步抵消 20px，出不出错卡片高度都不变 */
                .login-form .ant-form-item {
                    position: relative;
                    margin-bottom: 20px;
                }
                .login-form .ant-form-item-margin-offset {
                    margin-bottom: -20px !important;
                }
                .login-form .ant-form-item-label {
                    padding-bottom: 4px !important;
                    line-height: 16px;
                }
                .login-form .ant-form-item-label > label {
                    height: auto !important;
                    line-height: 16px !important;
                    color: #52607A; font-size: 12px; font-weight: 600;
                }
                .login-form .ant-form-item-additional {
                    min-height: 20px;
                    height: 20px;
                    overflow: hidden;
                }
                .login-form .ant-form-item-explain,
                .login-form .ant-form-item-extra {
                    min-height: 0;
                    padding-top: 2px;
                    font-size: 11px; line-height: 13px;
                }
                .login-form .ant-form-item-explain > div,
                .login-form .ant-form-item-explain-error {
                    font-size: 11px; line-height: 13px;
                }
                .login-field-row {
                    display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
                }
                .login-field-row .ant-form-item { min-width: 0; }
                .login-field-icon { color: #8A94AB; font-size: 14px; }
                /* 卡片是冷蓝纸面，字段用白色：控件比容器亮，不再出现"表单比卡片还重" */
                .login-card .ant-form .ant-input,
                .login-card .ant-form .ant-input-affix-wrapper,
                .login-card .ant-form .ant-input-number,
                .login-card .ant-form .ant-picker,
                .login-card .ant-form .ant-select:not(.ant-select-customize-input) .ant-select-selector {
                    background: #FFFFFF !important;
                    border-color: rgba(91, 141, 239, 0.22) !important;
                    box-shadow: none !important;
                }
                .login-card .ant-form .ant-input-affix-wrapper > input.ant-input {
                    background: transparent !important;
                }
                /* 内层 input 只负责文字：边框/焦点环/圆角全部交给外层 affix-wrapper，
                   否则聚焦时输入框内部会再出现一圈边框 */
                .login-card .ant-form .ant-input-affix-wrapper > input.ant-input,
                .login-card .ant-form .ant-input-affix-wrapper > input.ant-input:hover,
                .login-card .ant-form .ant-input-affix-wrapper > input.ant-input:focus {
                    border: 0 !important;
                    border-radius: 0 !important;
                    box-shadow: none !important;
                    outline: none !important;
                    background: transparent !important;
                }
                /* 字段高度收到 36px：卡片紧凑，控件不再是"大块头"。
                   圆角/字号跟全局 token 走（borderRadius 8、输入 14），
                   只有高度在登录页放大到 36，和 40 的主按钮配套 */
                .login-card .ant-form .ant-input,
                .login-card .ant-form .ant-input-affix-wrapper {
                    height: 36px;
                    border-radius: 8px;
                    font-size: 14px;
                }
                .login-card .ant-form .ant-input-affix-wrapper > input.ant-input {
                    height: 100%;
                    padding: 0;
                    font-size: 14px;
                }
                .login-card .ant-form .ant-input-affix-wrapper .ant-input-prefix {
                    margin-inline-end: 8px;
                }
                .login-card .ant-form .ant-input::placeholder,
                .login-card .ant-form .ant-select-selection-placeholder {
                    color: #A9B5CC !important;
                }
                .login-card .ant-form .ant-input-affix-wrapper:not(.ant-input-affix-wrapper-disabled):hover,
                .login-card .ant-form .ant-input:not(:disabled):hover {
                    border-color: rgba(91, 141, 239, 0.45) !important;
                }
                .login-card .ant-form .ant-input:focus,
                .login-card .ant-form .ant-input-affix-wrapper:focus-within,
                .login-card .ant-form .ant-select-focused .ant-select-selector {
                    background: #FFFFFF !important;
                    border-color: #5B8DEF !important;
                    box-shadow: 0 0 0 2px rgba(91, 141, 239, 0.12) !important;
                }
                /* 校验失败时字段自身给红边，和红字一起指认到具体输入框 */
                .login-card .ant-form .ant-form-item-has-error .ant-input,
                .login-card .ant-form .ant-form-item-has-error .ant-input-affix-wrapper {
                    border-color: #EE7A80 !important;
                    background: #FFFCFC !important;
                }
                .login-card .ant-form .ant-form-item-has-error .ant-input-affix-wrapper:focus-within,
                .login-card .ant-form .ant-form-item-has-error .ant-input:focus {
                    border-color: #E45C64 !important;
                    box-shadow: 0 0 0 2px rgba(228, 92, 100, 0.12) !important;
                }
                .login-submit.ant-btn-primary {
                    height: 40px;
                    margin-top: 2px;
                    border: 0;
                    border-radius: 10px;
                    font-size: 13.5px; font-weight: 600;
                    background: linear-gradient(135deg, #5B8DEF 0%, #4C7DE7 55%, #6E6BF0 100%) !important;
                    box-shadow:
                        inset 0 1px 0 rgba(255,255,255,0.30),
                        0 14px 26px -14px rgba(76,125,231,0.80) !important;
                    transition: transform 0.18s ease, box-shadow 0.18s ease;
                }
                .login-submit.ant-btn-primary:not(:disabled):hover {
                    background: linear-gradient(135deg, #6B9BF5 0%, #5B8BF0 55%, #7E7BF5 100%) !important;
                    transform: translateY(-1px);
                    box-shadow:
                        inset 0 1px 0 rgba(255,255,255,0.30),
                        0 18px 30px -14px rgba(76,125,231,0.90) !important;
                }
                .login-submit.ant-btn-primary:not(:disabled):active {
                    transform: translateY(0);
                }
                .login-card-foot {
                    display: flex; align-items: center; justify-content: space-between; gap: 10px;
                    margin-top: 18px; padding-top: 12px;
                    border-top: 1px solid rgba(91,141,239,0.10);
                    color: #A3ADC2; font-size: 10.5px;
                    font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
                }
                @media (max-width: 1023px) {
                    .login-stage {
                        grid-template-columns: 1fr;
                        max-width: 520px;
                        padding: 28px 20px;
                    }
                    .login-hero { display: none; }
                    .login-panel { max-width: 470px; margin: 0 auto; }
                }
                /* 1700 以上整体提一档：舞台、字号、行宽、卡片同步放大，
                   否则在 2K/4K 上构图会显得"缩在中间" */
                @media (min-width: 1700px) {
                    .login-stage {
                        max-width: 1480px;
                        padding: clamp(48px, 6vh, 88px) clamp(64px, 5vw, 96px);
                        gap: clamp(108px, 7vw, 132px);
                    }
                    .login-hero-title { font-size: clamp(52px, 3.4vw, 58px); }
                    .login-hero-sub {
                        max-width: 600px; font-size: 15px; line-height: 1.9;
                    }
                    .login-feature-list { max-width: 620px; }
                    .login-feature { padding: 16px 0; }
                    .login-feature-title { font-size: 14.5px; }
                    .login-feature-desc { font-size: 12.5px; }
                    .login-panel { max-width: 500px; }

                    /* 卡片 470 → 500，卡内文字同步放大一档；
                       字段间距仍是 20px（报错带），所以高度机制不受影响 */
                    .login-mark { width: 42px; height: 42px; border-radius: 12px; }
                    .login-brand-eyebrow { font-size: 9.5px; }
                    .login-brand-name { font-size: 14px; }
                    .login-secure { font-size: 10px; padding: 3px 8px; }
                    .login-switch button { height: 36px; font-size: 16px; }
                    .login-card-title { font-size: 21px; line-height: 28px; }
                    .login-card-sub { font-size: 13px; line-height: 18px; }
                    .login-cap { height: 23px; padding: 0 9px; font-size: 11px; }
                    .login-card .login-form .ant-form-item-label > label { font-size: 13.5px; }
                    .login-card .ant-form .ant-input,
                    .login-card .ant-form .ant-input-affix-wrapper {
                        height: 38px; font-size: 15px;
                    }
                    .login-card .ant-form .ant-input-affix-wrapper > input.ant-input {
                        font-size: 15px;
                    }
                    .login-field-icon { font-size: 15px; }
                    .login-submit.ant-btn-primary { height: 42px; font-size: 14.5px; }
                    .login-form .ant-form-item-explain,
                    .login-form .ant-form-item-explain > div,
                    .login-form .ant-form-item-explain-error { font-size: 11.5px; }
                    .login-card-foot { font-size: 11px; }
                }
                @media (max-width: 480px) {
                    .login-card { padding: 22px 18px 18px; border-radius: 16px; }
                    .login-card-title { font-size: 19px; }
                    .login-secure { display: none; }
                }
                @media (prefers-reduced-motion: reduce) {
                    .login-submit.ant-btn-primary { transition: none; }
                }
            `}</style>

            <div className="login-bg" style={{ backgroundImage: `url(${canvasBg})` }} />
            <DecoConstellation id="login-deco" className="login-deco" />

            <div className="login-stage">
                <section className="login-hero">
                    <div className="login-eyebrow">
                        <span className="login-eyebrow-dot" aria-hidden />
                        Enterprise Knowledge Graph
                    </div>
                    <h1 className="login-hero-title">
                        把企业知识<br />
                        <span>沉淀为可计算的本体</span>
                    </h1>
                    <p className="login-hero-sub">
                        基于 AI 的本体构建、知识图谱与溯源治理一站式流水线。
                        从文档解析到骨架抽取、实例探索与资产发布，让散落的业务知识变成可检索、可推理、可运营的语义资产。
                    </p>
                    <div className="login-feature-list">
                        {FEATURES.map(f => (
                            <div className="login-feature" key={f.index}>
                                <span className="login-feature-index tk-mono">{f.index}</span>
                                <div>
                                    <div className="login-feature-title">{f.title}</div>
                                    <div className="login-feature-desc">{f.desc}</div>
                                </div>
                            </div>
                        ))}
                    </div>
                    <div className="login-hero-meta">KNOWLEDGE · MODELING · GOVERNANCE</div>
                </section>

                <section className="login-panel">
                    <div className="login-card">
                        <DecoConstellation id="login-card-deco" className="login-card-deco" />
                        <div className="login-card-top">
                            <div className="login-brand">
                                <span className="login-mark">
                                    <BrandMark size={22} strokeWidth={4} />
                                </span>
                                <span className="login-brand-text">
                                    <span className="login-brand-eyebrow">ONTOLOGY PLATFORM</span>
                                    <span className="login-brand-name">本体平台</span>
                                </span>
                            </div>
                            <span className="login-secure">
                                <SafetyCertificateOutlined /> SECURE
                            </span>
                        </div>

                        <div className="login-switch" role="tablist" aria-label="登录或注册">
                            <button
                                type="button"
                                role="tab"
                                aria-selected={activeTab === 'login'}
                                className={activeTab === 'login' ? 'is-active' : ''}
                                onClick={() => setActiveTab('login')}
                            >
                                登录
                            </button>
                            <button
                                type="button"
                                role="tab"
                                aria-selected={activeTab === 'register'}
                                className={activeTab === 'register' ? 'is-active' : ''}
                                onClick={() => setActiveTab('register')}
                            >
                                注册
                            </button>
                        </div>

                        <div className="login-card-title-row">
                            <span className="login-title-mark" aria-hidden />
                            <div className="login-card-title">
                                {activeTab === 'login' ? '欢迎回来' : '创建账号'}
                            </div>
                        </div>
                        <p className="login-card-sub">
                            {activeTab === 'login'
                                ? '登录后进入工作台，继续本体建模与知识治理。'
                                : '注册后即可创建工作台项目；管理权限请联系管理员。'}
                        </p>
                        <div className="login-capabilities">
                            {CAPABILITIES[activeTab].map(cap => (
                                <span className="login-cap" key={cap}>
                                    <span className="login-cap-dot" aria-hidden />
                                    {cap}
                                </span>
                            ))}
                        </div>

                        {renderForm(activeTab)}

                        <div className="login-card-foot">
                            <span>ONTOLOGY SYSTEM</span>
                            <span>企业语义平台 · 安全登录</span>
                        </div>
                    </div>
                </section>
            </div>
        </div>
    );
};

export default LoginPage;

import React, { useState } from 'react';
import { Form, Input, Button, Card, Tabs, message } from 'antd';
import { UserOutlined, LockOutlined, MailOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { authAPI } from '../api/auth';

const LoginPage: React.FC = () => {
    const navigate = useNavigate();
    const [loading, setLoading] = useState(false);
    const [activeTab, setActiveTab] = useState('login');

    const handleLogin = async (values: any) => {
        setLoading(true);
        try {
            const response = await authAPI.login({
                username: values.username,
                password: values.password,
            });

            localStorage.setItem('access_token', response.access_token);
            localStorage.setItem('user', JSON.stringify(response.user));

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
                email: values.email,
                password: values.password,
            });

            localStorage.setItem('access_token', response.access_token);
            localStorage.setItem('user', JSON.stringify(response.user));

            message.success('注册成功！');
            navigate('/');
        } catch (error: any) {
            message.error(error.response?.data?.detail || '注册失败，请稍后重试');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 via-purple-50 to-pink-50">
            {/* 背景装饰 */}
            <div className="absolute inset-0 overflow-hidden pointer-events-none">
                <div className="absolute -top-40 -right-40 w-80 h-80 bg-purple-300 rounded-full mix-blend-multiply filter blur-xl opacity-70 animate-blob"></div>
                <div className="absolute -bottom-40 -left-40 w-80 h-80 bg-blue-300 rounded-full mix-blend-multiply filter blur-xl opacity-70 animate-blob animation-delay-2000"></div>
                <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 w-80 h-80 bg-pink-300 rounded-full mix-blend-multiply filter blur-xl opacity-70 animate-blob animation-delay-4000"></div>
            </div>

            <Card className="w-full max-w-md shadow-2xl rounded-2xl relative z-10">
                {/* Logo 区域 */}
                <div className="text-center mb-8">
                    <div className="inline-flex items-center justify-center w-16 h-16 bg-gradient-to-br from-blue-500 to-purple-600 rounded-2xl mb-4">
                        <span className="text-white font-bold text-2xl">K</span>
                    </div>
                    <h1 className="text-3xl font-bold bg-gradient-to-r from-blue-600 to-purple-600 bg-clip-text text-transparent">
                        Knora 本体平台
                    </h1>
                    <p className="text-gray-500 mt-2">知识图谱构建与管理系统</p>
                </div>

                <Tabs
                    activeKey={activeTab}
                    onChange={setActiveTab}
                    centered
                    items={[
                        {
                            key: 'login',
                            label: '登录',
                            children: (
                                <Form onFinish={handleLogin} layout="vertical" size="large">
                                    <Form.Item
                                        name="username"
                                        rules={[{ required: true, message: '请输入用户名' }]}
                                    >
                                        <Input
                                            prefix={<UserOutlined className="text-gray-400" />}
                                            placeholder="用户名"
                                            className="rounded-lg"
                                        />
                                    </Form.Item>

                                    <Form.Item
                                        name="password"
                                        rules={[{ required: true, message: '请输入密码' }]}
                                    >
                                        <Input.Password
                                            prefix={<LockOutlined className="text-gray-400" />}
                                            placeholder="密码"
                                            className="rounded-lg"
                                        />
                                    </Form.Item>

                                    <Form.Item>
                                        <Button
                                            type="primary"
                                            htmlType="submit"
                                            loading={loading}
                                            block
                                            className="h-12 rounded-lg bg-gradient-to-r from-blue-500 to-purple-600 border-0 hover:from-blue-600 hover:to-purple-700"
                                        >
                                            登录
                                        </Button>
                                    </Form.Item>
                                </Form>
                            ),
                        },
                        {
                            key: 'register',
                            label: '注册',
                            children: (
                                <Form onFinish={handleRegister} layout="vertical" size="large">
                                    <Form.Item
                                        name="username"
                                        rules={[{ required: true, message: '请输入用户名' }]}
                                    >
                                        <Input
                                            prefix={<UserOutlined className="text-gray-400" />}
                                            placeholder="用户名"
                                            className="rounded-lg"
                                        />
                                    </Form.Item>

                                    <Form.Item
                                        name="email"
                                        rules={[
                                            { required: true, message: '请输入邮箱' },
                                            { type: 'email', message: '请输入有效的邮箱地址' },
                                        ]}
                                    >
                                        <Input
                                            prefix={<MailOutlined className="text-gray-400" />}
                                            placeholder="邮箱"
                                            className="rounded-lg"
                                        />
                                    </Form.Item>

                                    <Form.Item
                                        name="password"
                                        rules={[
                                            { required: true, message: '请输入密码' },
                                            { min: 6, message: '密码至少6位' },
                                        ]}
                                    >
                                        <Input.Password
                                            prefix={<LockOutlined className="text-gray-400" />}
                                            placeholder="密码"
                                            className="rounded-lg"
                                        />
                                    </Form.Item>

                                    <Form.Item
                                        name="confirmPassword"
                                        dependencies={['password']}
                                        rules={[
                                            { required: true, message: '请确认密码' },
                                            ({ getFieldValue }) => ({
                                                validator(_, value) {
                                                    if (!value || getFieldValue('password') === value) {
                                                        return Promise.resolve();
                                                    }
                                                    return Promise.reject(new Error('两次输入的密码不一致'));
                                                },
                                            }),
                                        ]}
                                    >
                                        <Input.Password
                                            prefix={<LockOutlined className="text-gray-400" />}
                                            placeholder="确认密码"
                                            className="rounded-lg"
                                        />
                                    </Form.Item>

                                    <Form.Item>
                                        <Button
                                            type="primary"
                                            htmlType="submit"
                                            loading={loading}
                                            block
                                            className="h-12 rounded-lg bg-gradient-to-r from-blue-500 to-purple-600 border-0 hover:from-blue-600 hover:to-purple-700"
                                        >
                                            注册
                                        </Button>
                                    </Form.Item>
                                </Form>
                            ),
                        },
                    ]}
                />
            </Card>
        </div>
    );
};

export default LoginPage;

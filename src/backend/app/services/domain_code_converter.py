# app/services/domain_code_converter.py - 领域代码转换器
# 功能：将中文主题转换为英文知识域代码，支持拼音转换和常见主题映射

"""
中文到英文知识域代码的转换工具
支持拼音转换和常见主题映射
"""

def chinese_to_domain_code(chinese_text: str) -> str:
    """
    将中文主题转换为英文知识域代码
    
    Args:
        chinese_text: 中文主题名称
        
    Returns:
        英文知识域代码（大写，下划线分隔）
    """
    if not chinese_text or not chinese_text.strip():
        return None
    
    text = chinese_text.strip()
    
    # 常见主题映射表
    TOPIC_MAP = {
        # 技术主题
        "人工智能": "TOPIC_AI",
        "机器学习": "TOPIC_ML",
        "深度学习": "TOPIC_DL",
        "区块链": "TOPIC_BLOCKCHAIN",
        "量子计算": "TOPIC_QUANTUM",
        "云计算": "TOPIC_CLOUD",
        "大数据": "TOPIC_BIGDATA",
        "物联网": "TOPIC_IOT",
        "5G": "TOPIC_5G",
        "网络安全": "TOPIC_SECURITY",
        "数据库": "TOPIC_DATABASE",
        
        # 金融主题
        "理财产品": "TOPIC_WEALTH",
        "基金": "TOPIC_FUND",
        "股票": "TOPIC_STOCK",
        "债券": "TOPIC_BOND",
        "保险": "TOPIC_INSURANCE",
        "银行": "TOPIC_BANK",
        "证券": "TOPIC_SECURITIES",
        
        # 部门
        "人力资源": "DEPT_HR",
        "人力资源部": "DEPT_HR",
        "信息技术": "DEPT_IT",
        "信息技术部": "DEPT_IT",
        "IT部": "DEPT_IT",
        "财务": "DEPT_FINANCE",
        "财务部": "DEPT_FINANCE",
        "市场": "DEPT_MARKETING",
        "市场部": "DEPT_MARKETING",
        "销售": "DEPT_SALES",
        "销售部": "DEPT_SALES",
        "研发": "DEPT_RD",
        "研发部": "DEPT_RD",
        "运营": "DEPT_OPS",
        "运营部": "DEPT_OPS",
        
        # 地区
        "北京": "REGION_BEIJING",
        "上海": "REGION_SHANGHAI",
        "广州": "REGION_GUANGZHOU",
        "深圳": "REGION_SHENZHEN",
        "华北": "REGION_NORTH",
        "华南": "REGION_SOUTH",
        "华东": "REGION_EAST",
        "华西": "REGION_WEST",
        
        # 行业
        "医疗": "INDUSTRY_HEALTHCARE",
        "教育": "INDUSTRY_EDUCATION",
        "制造": "INDUSTRY_MANUFACTURING",
        "零售": "INDUSTRY_RETAIL",
        "物流": "INDUSTRY_LOGISTICS",
        "房地产": "INDUSTRY_REALESTATE",
    }
    
    # 1. 检查是否在映射表中
    if text in TOPIC_MAP:
        return TOPIC_MAP[text]
    
    # 2. 检查是否已经是英文（允许直接使用）
    if text.encode('utf-8').isalpha() or '_' in text:
        # 已经是英文，转换为大写
        return text.upper().replace(' ', '_').replace('-', '_')
    
    # 3. 尝试使用拼音转换
    try:
        from pypinyin import lazy_pinyin, Style
        
        # 转换为拼音
        pinyin_list = lazy_pinyin(text, style=Style.NORMAL)
        pinyin_code = '_'.join(pinyin_list).upper()
        
        # 添加TOPIC前缀
        return f"TOPIC_{pinyin_code}"
    except ImportError:
        # 如果没有安装pypinyin，使用简单的编码方式
        # 将中文转换为拼音首字母
        import re
        
        # 简单的首字母映射（不完整，但可以作为后备方案）
        PINYIN_INITIAL = {
            '人': 'R', '工': 'G', '智': 'Z', '能': 'N',
            '机': 'J', '器': 'Q', '学': 'X', '习': 'X',
            '深': 'S', '度': 'D',
            '区': 'Q', '块': 'K', '链': 'L',
            '量': 'L', '子': 'Z', '计': 'J', '算': 'S',
            '云': 'Y',
            '大': 'D', '数': 'S', '据': 'J',
            '物': 'W', '联': 'L', '网': 'W',
        }
        
        initials = ''.join([PINYIN_INITIAL.get(char, 'X') for char in text if char in PINYIN_INITIAL])
        
        if initials:
            return f"TOPIC_{initials}"
        else:
            # 最后的后备方案：使用时间戳
            import time
            timestamp = str(int(time.time()))[-6:]
            return f"DOMAIN_{timestamp}"


def suggest_domain_code(text: str) -> tuple[str, bool]:
    """
    为用户输入提供知识域代码建议
    
    Args:
        text: 用户输入的文本
        
    Returns:
        (建议的代码, 是否需要确认)
    """
    if not text or not text.strip():
        return None, False
    
    code = chinese_to_domain_code(text)
    
    # 如果是从映射表得到的，不需要确认
    # 如果是自动生成的拼音，需要用户确认
    needs_confirmation = "TOPIC_" in code and len(code) > 15
    
    return code, needs_confirmation


# 测试用例
if __name__ == "__main__":
    test_cases = [
        "人工智能",
        "机器学习",
        "区块链",
        "人力资源部",
        "北京",
        "医疗",
        "PRODUCT_A",  # 已经是英文
        "自定义主题",  # 不在映射表中
    ]
    
    print("中文到知识域代码转换测试：\n")
    for case in test_cases:
        code, needs_confirm = suggest_domain_code(case)
        confirm_mark = " (需要确认)" if needs_confirm else ""
        print(f"{case:15s} → {code}{confirm_mark}")
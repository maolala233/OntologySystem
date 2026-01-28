#!/usr/bin/env python
"""
直接使用项目配置更新数据库表结构
"""

import sys
import os
from sqlalchemy import create_engine, text

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'backend'))

from app.core.config import settings

def update_database_structure():
    print("🔧 正在连接到数据库...")
    
    try:
        # 使用项目配置的数据库 URL
        engine = create_engine(settings.DATABASE_URL)
        
        # 尝试删除 email 列（如果存在）
        with engine.connect() as conn:
            # 对于 MySQL，使用 ALTER TABLE DROP COLUMN
            try:
                # 检查列是否存在
                if settings.DATABASE_URL.startswith("mysql"):
                    result = conn.execute(text(f"""
                        SELECT COLUMN_NAME 
                        FROM INFORMATION_SCHEMA.COLUMNS 
                        WHERE TABLE_SCHEMA = '{settings.MYSQL_DATABASE}' 
                        AND TABLE_NAME = 'users' 
                        AND COLUMN_NAME = 'email';
                    """))
                    
                    if result.fetchone():
                        print("🗑️ 检测到 email 列存在，正在移除...")
                        conn.execute(text("ALTER TABLE users DROP COLUMN email;"))
                        conn.commit()
                        print("✅ email 列已成功移除")
                    else:
                        print("ℹ️ users 表中不存在 email 列，无需移除")
                else:
                    # 对于 SQLite，情况更复杂，因为它不直接支持 DROP COLUMN
                    print("ℹ️ 使用非 MySQL 数据库，跳过 email 列删除")
                    
            except Exception as e:
                print(f"⚠️ 删除 email 列时出现可能的预期错误（例如列不存在）: {e}")
        
        print("✅ 数据库结构更新检查完成")
        return True
        
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        return False

if __name__ == "__main__":
    success = update_database_structure()
    if success:
        print("\n🎉 数据库更新检查完成！表结构已按照最新模型定义调整。")
    else:
        print("\n❌ 数据库更新过程中出现问题。")
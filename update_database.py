#!/usr/bin/env python
"""
更新数据库表结构，移除 users 表中的 email 字段
"""

import pymysql
from sqlalchemy import create_engine, MetaData, Table
from sqlalchemy.exc import OperationalError
import os
import sys

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'backend'))

from app.core.config import settings
from app.infrastructure.database import Base

def update_database_structure():
    print("🔧 正在连接到 MySQL 数据库...")
    
    # 首先尝试直接删除 email 列（如果存在）
    try:
        connection = pymysql.connect(
            host=settings.MYSQL_HOST,
            port=settings.MYSQL_PORT,
            user=settings.MYSQL_USER,
            password=settings.MYSQL_PASSWORD
        )
        
        with connection.cursor() as cursor:
            # 选择数据库
            cursor.execute(f"USE {settings.MYSQL_DATABASE};")
            
            # 检查 email 列是否存在
            cursor.execute("""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'users' AND COLUMN_NAME = 'email';
            """, (settings.MYSQL_DATABASE,))
            
            result = cursor.fetchone()
            
            if result:
                print("🗑️ 检测到 email 列存在，正在移除...")
                cursor.execute("ALTER TABLE users DROP COLUMN email;")
                print("✅ email 列已成功移除")
            else:
                print("ℹ️ users 表中不存在 email 列，无需移除")
        
        connection.commit()
        connection.close()
        print("✅ 数据库结构更新完成")
        
    except Exception as e:
        print(f"❌ 数据库结构更新失败: {e}")
        return False
    
    # 重新创建表结构以确保其他更改生效
    try:
        print("🔨 正在应用 SQLAlchemy 模型...")
        engine = create_engine(settings.DATABASE_URL)
        Base.metadata.create_all(bind=engine)
        print("✅ SQLAlchemy 模型已应用")
        return True
    except Exception as e:
        print(f"❌ SQLAlchemy 模型应用失败: {e}")
        return False

if __name__ == "__main__":
    success = update_database_structure()
    if success:
        print("\n🎉 数据库更新成功！现在用户注册不再需要邮箱字段。")
    else:
        print("\n❌ 数据库更新过程中出现问题。")
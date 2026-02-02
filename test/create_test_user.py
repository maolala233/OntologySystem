"""
创建测试用户的脚本
运行: python create_test_user.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.infrastructure.database import SessionLocal, User, init_db

def create_test_users():
    init_db()  # 确保表已创建
    
    db = SessionLocal()
    
    try:
        # 检查是否已存在测试用户
        existing_user = db.query(User).filter(User.username == "admin").first()
        if existing_user:
            print("✅ 测试用户 'admin' 已存在")
            return
        
        # 创建测试用户
        test_users = [
            User(
                username="admin",
                hashed_password="123456",  # 实际应使用加密
                is_active=True
            ),
            User(
                username="testuser",
                hashed_password="123456",
                is_active=True
            )
        ]
        
        for user in test_users:
            db.add(user)
        
        db.commit()
        print("✅ 测试用户创建成功！")
        print("   用户名: admin, 密码: 123456")
        print("   用户名: testuser, 密码: 123456")
        
    except Exception as e:
        print(f"❌ 创建测试用户失败: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    create_test_users()

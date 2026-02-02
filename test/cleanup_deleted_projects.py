"""
清理已删除项目在图数据库中的残留数据
此脚本用于清理那些在关系数据库中已被删除但在图数据库中仍有残留的项目数据
"""

from sqlalchemy import create_engine, text
from app.core.config import settings
from app.infrastructure.neo4j_client import neo4j_client
from app.infrastructure.database import SessionLocal


def cleanup_orphaned_graph_data():
    """
    清理图数据库中孤立的项目数据
    """
    print("🔍 开始清理图数据库中孤立的项目数据...")
    
    # 获取所有存在的项目ID
    db = SessionLocal()
    try:
        result = db.execute(text("SELECT id FROM projects"))
        existing_project_ids = {row[0] for row in result.fetchall()}
        print(f"📊 数据库中存在的项目数量: {len(existing_project_ids)}")
    finally:
        db.close()
    
    # 从Neo4j获取所有有project_id属性的节点
    # 注意：这需要连接到Neo4j数据库来获取这些信息
    print("🔍 查询图数据库中的项目数据...")
    
    if not neo4j_client.driver:
        print("⚠️ 无法连接到Neo4j数据库，请检查配置")
        return
    
    with neo4j_client.driver.session() as session:
        # 获取所有不同的project_id（使用正确的Neo4j语法）
        result = session.run("MATCH (n) WHERE n.project_id IS NOT NULL RETURN DISTINCT n.project_id AS project_id")
        neo4j_project_ids = {record["project_id"] for record in result}
        print(f"📊 图数据库中存在的项目数量: {len(neo4j_project_ids)}")
        
        # 找出图数据库中有但关系数据库中没有的项目ID
        orphaned_project_ids = neo4j_project_ids - existing_project_ids
        print(f"🧹 需要清理的孤立项目数量: {len(orphaned_project_ids)}")
        
        if orphaned_project_ids:
            print(f"📋 需要清理的项目ID: {sorted(orphaned_project_ids)}")
            
            for project_id in orphaned_project_ids:
                print(f"🗑️ 正在清理项目 {project_id} 的图数据库数据...")
                try:
                    neo4j_client.delete_project_data(project_id)
                    print(f"✅ 项目 {project_id} 的图数据库数据已清理")
                except Exception as e:
                    print(f"❌ 清理项目 {project_id} 数据时出错: {str(e)}")
        else:
            print("✅ 没有发现需要清理的孤立项目数据")
    
    print("🎉 清理完成!")


if __name__ == "__main__":
    cleanup_orphaned_graph_data()
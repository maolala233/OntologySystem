"""
注入流程协调服务
协调整个注入流程：文档解析 -> Schema构建 -> 实例构建 -> ES注入
"""
import asyncio
import logging
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime
from app.core.logging import logger
from app.core.config import settings
from app.core.exceptions import GraphInjectorException
from app.services.schema_service import SchemaBuilder
from app.services.instance_service import InstanceBuilder
from app.services.inject_service import ESInjector, RAGFlowGraphInjector


class TaskManager:
    """
    异步任务管理器
    跟踪所有注入任务的状态和进度
    """

    def __init__(self):
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.logger = logging.getLogger("graph_injector.task_manager")

    def create_task(self, task_id: str = None) -> str:
        """创建新任务"""
        if not task_id:
            task_id = str(uuid.uuid4())

        self.tasks[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "progress": 0,
            "message": "任务已创建，等待处理",
            "result": None,
            "error_message": None,
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "steps": [],
        }
        self.logger.info(f"任务已创建: {task_id}")
        return task_id

    def update_task(self, task_id: str, status: str = None, progress: int = None,
                   message: str = None, result: Dict[str, Any] = None,
                   error_message: str = None, step: str = None):
        """更新任务状态"""
        if task_id not in self.tasks:
            return

        task = self.tasks[task_id]
        if status:
            task["status"] = status
        if progress is not None:
            task["progress"] = progress
        if message:
            task["message"] = message
        if result is not None:
            task["result"] = result
        if error_message:
            task["error_message"] = error_message
        if step:
            task["steps"].append({
                "step": step,
                "timestamp": datetime.now().isoformat(),
            })

        self.logger.debug(f"任务更新: {task_id} - status={task['status']}, "
                         f"progress={task['progress']}%, message={task['message']}")

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务信息"""
        return self.tasks.get(task_id)

    def complete_task(self, task_id: str, result: Dict[str, Any]):
        """完成任务"""
        self.update_task(
            task_id,
            status="completed",
            progress=100,
            message="任务完成",
            result=result,
        )
        self.tasks[task_id]["completed_at"] = datetime.now().isoformat()

    def fail_task(self, task_id: str, error_message: str):
        """标记任务失败"""
        self.update_task(
            task_id,
            status="error",
            message="任务失败",
            error_message=error_message,
        )
        self.tasks[task_id]["completed_at"] = datetime.now().isoformat()


# 全局任务管理器实例
task_manager = TaskManager()


class InjectionOrchestrator:
    """
    注入流程协调器
    协调整个知识图谱构建和注入流程
    """

    def __init__(self):
        self.logger = logging.getLogger("graph_injector.orchestrator")
        self.schema_builder = SchemaBuilder()
        self.instance_builder = InstanceBuilder()
        self.es_injector = ESInjector()
        self.ragflow_injector = RAGFlowGraphInjector()

    async def execute_injection(self, task_id: str, text_content: str,
                                chunk_size: int, overlap_percentage: int,
                                use_existing_schema: bool, existing_schema_path: Optional[str],
                                ragflow_kb_id: str, ragflow_tenant_id: str,
                                ragflow_api_key: str,
                                additional_instructions: Optional[str] = None) -> Dict[str, Any]:
        """
        执行完整的注入流程

        Args:
            task_id: 任务ID
            text_content: 文档文本内容
            chunk_size: 每个chunk的字符长度
            overlap_percentage: chunk之间的重叠百分比
            use_existing_schema: 是否使用已有Schema
            existing_schema_path: 已有Schema文件路径
            ragflow_kb_id: RAGFlow知识库ID
            ragflow_tenant_id: RAGFlow租户ID
            ragflow_api_key: RAGFlow API Key
            additional_instructions: 额外指令

        Returns:
            注入结果
        """
        try:
            task_manager.update_task(task_id, status="processing", progress=0,
                                   message="开始执行注入流程")

            doc_id = f"graph_injector_{task_id[:8]}"

            if use_existing_schema and existing_schema_path:
                task_manager.update_task(task_id, progress=10,
                                       message=f"加载已有Schema: {existing_schema_path}")
                self.logger.info(f"使用已有Schema: {existing_schema_path}")
                schema = self.schema_builder.load_schema(existing_schema_path)
                task_manager.update_task(task_id, step="schema_loaded")
            else:
                task_manager.update_task(task_id, progress=10,
                                       message="正在从文档构建Schema...")
                self.logger.info("开始从文档构建Schema")
                schema = await self.schema_builder.build_schema(
                    text_content, chunk_size, overlap_percentage, additional_instructions
                )
                task_manager.update_task(task_id, step="schema_built")

                schema_path = self.schema_builder.save_schema(schema)
                self.logger.info(f"Schema已保存: {schema_path}")
                task_manager.update_task(task_id, progress=30,
                                       message=f"Schema构建完成，已保存: {schema_path}")

            task_manager.update_task(task_id, progress=40,
                                   message="正在构建Ontology对象和链接实例...")
            self.logger.info("开始构建Ontology对象和链接实例")
            instance_result = await self.instance_builder.build_instances(
                text_content, schema, chunk_size, overlap_percentage, additional_instructions
            )
            task_manager.update_task(task_id, step="instances_built")
            task_manager.update_task(task_id, progress=60,
                                   message=f"Ontology实例构建完成: "
                                          f"对象={len(instance_result['nodes'])}, "
                                          f"链接={len(instance_result.get('edges', instance_result.get('relationships', [])))}")

            task_manager.update_task(task_id, progress=70,
                                   message="正在注入数据到Elasticsearch...")
            self.logger.info("开始注入数据到ES")
            edges = instance_result.get('edges', instance_result.get('relationships', []))
            injection_result = await self.ragflow_injector.upload_and_inject(
                text_contents=[text_content],
                kb_id=ragflow_kb_id,
                tenant_id=ragflow_tenant_id,
                api_key=ragflow_api_key,
                doc_id=doc_id,
                entities=instance_result["nodes"],
                relationships=edges,
            )
            task_manager.update_task(task_id, step="data_injected")
            task_manager.update_task(task_id, progress=90,
                                   message="ES注入完成")

            object_types_count = len(schema.get("object_types", schema.get("entity_types", [])))
            link_types_count = len(schema.get("link_types", schema.get("relation_types", [])))
            action_types_count = len(schema.get("action_types", []))

            final_result = {
                "doc_id": doc_id,
                "schema": {
                    "object_types": object_types_count,
                    "link_types": link_types_count,
                    "action_types": action_types_count,
                },
                "instances": {
                    "entities": len(instance_result["nodes"]),
                    "relationships": len(edges),
                },
                "injection": injection_result.get("injection", {}),
                "schema_path": schema_path if not use_existing_schema else existing_schema_path,
            }

            task_manager.complete_task(task_id, final_result)
            task_manager.update_task(task_id, progress=100,
                                   message="注入流程全部完成")

            self.logger.info(f"注入流程完成: task_id={task_id}")
            return final_result

        except Exception as e:
            error_msg = f"注入流程失败: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            task_manager.fail_task(task_id, error_msg)
            raise GraphInjectorException(error_msg)

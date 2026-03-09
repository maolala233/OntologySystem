# src/backend/app/infrastructure/task_manager.py
# 任务状态管理器 - 用于管理 PDF 解析任务的进度和取消操作

import asyncio
import threading
from typing import Dict, Optional, Any, Callable
from datetime import datetime
from enum import Enum
import uuid


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskProgress:
    """任务进度数据类"""
    def __init__(
        self,
        task_id: str,
        status: TaskStatus = TaskStatus.PENDING,
        progress: float = 0.0,
        message: str = "",
        detail: str = "",
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        error: Optional[str] = None,
        result: Optional[Any] = None,
    ):
        self.task_id = task_id
        self.status = status
        self.progress = progress
        self.message = message
        self.detail = detail
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()
        self.completed_at = completed_at
        self.error = error
        self.result = result

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，用于 JSON 序列化"""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "detail": self.detail,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "result": self.result,
        }


class TaskManager:
    """
    任务管理器 - 单例模式
    负责管理所有任务的进度、状态和取消操作
    """
    _instance: Optional["TaskManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "TaskManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._tasks: Dict[str, TaskProgress] = {}
        self._cancel_flags: Dict[str, bool] = {}
        self._callbacks: Dict[str, Callable] = {}
        self._lock = threading.Lock()

    def create_task(
        self,
        task_id: Optional[str] = None,
        message: str = "",
        detail: str = "",
    ) -> str:
        """创建新任务"""
        if task_id is None:
            task_id = str(uuid.uuid4())
        
        with self._lock:
            self._tasks[task_id] = TaskProgress(
                task_id=task_id,
                status=TaskStatus.PENDING,
                message=message,
                detail=detail,
            )
            self._cancel_flags[task_id] = False
        
        return task_id

    def start_task(self, task_id: str, message: str = "", detail: str = ""):
        """开始任务"""
        with self._lock:
            if task_id not in self._tasks:
                self.create_task(task_id, message, detail)
            
            task = self._tasks[task_id]
            task.status = TaskStatus.RUNNING
            task.updated_at = datetime.now()
            if message:
                task.message = message
            if detail:
                task.detail = detail

    def update_progress(
        self,
        task_id: str,
        progress: float,
        message: str = "",
        detail: str = "",
    ):
        """更新任务进度"""
        with self._lock:
            if task_id not in self._tasks:
                return
            
            task = self._tasks[task_id]
            task.progress = max(0.0, min(1.0, progress))
            task.updated_at = datetime.now()
            if message:
                task.message = message
            if detail:
                task.detail = detail

    def complete_task(self, task_id: str, result: Optional[Any] = None, message: str = ""):
        """完成任务"""
        with self._lock:
            if task_id not in self._tasks:
                return
            
            task = self._tasks[task_id]
            task.status = TaskStatus.COMPLETED
            task.progress = 1.0
            task.completed_at = datetime.now()
            task.updated_at = datetime.now()
            if message:
                task.message = message
            if result is not None:
                task.result = result

    def fail_task(self, task_id: str, error: str, message: str = ""):
        """任务失败"""
        with self._lock:
            if task_id not in self._tasks:
                return
            
            task = self._tasks[task_id]
            task.status = TaskStatus.FAILED
            task.error = error
            task.completed_at = datetime.now()
            task.updated_at = datetime.now()
            if message:
                task.message = message

    def cancel_task(self, task_id: str, message: str = "用户取消任务"):
        """取消任务"""
        with self._lock:
            if task_id not in self._tasks:
                return False
            
            task = self._tasks[task_id]
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False
            
            task.status = TaskStatus.CANCELLED
            task.message = message
            task.completed_at = datetime.now()
            task.updated_at = datetime.now()
            self._cancel_flags[task_id] = True
            
            # 触发回调
            if task_id in self._callbacks:
                try:
                    self._callbacks[task_id](task_id, TaskStatus.CANCELLED)
                except Exception:
                    pass
            
            return True

    def is_cancelled(self, task_id: str) -> bool:
        """检查任务是否被取消"""
        with self._lock:
            return self._cancel_flags.get(task_id, False)

    def check_cancelled(self, task_id: str):
        """检查任务是否被取消，如果取消则抛出异常"""
        if self.is_cancelled(task_id):
            raise TaskCancelledError(f"Task {task_id} was cancelled")

    def get_task(self, task_id: str) -> Optional[TaskProgress]:
        """获取任务进度"""
        with self._lock:
            return self._tasks.get(task_id)

    def get_all_tasks(self) -> Dict[str, TaskProgress]:
        """获取所有任务"""
        with self._lock:
            return dict(self._tasks)

    def remove_task(self, task_id: str):
        """移除任务"""
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
            if task_id in self._cancel_flags:
                del self._cancel_flags[task_id]
            if task_id in self._callbacks:
                del self._callbacks[task_id]

    def register_callback(self, task_id: str, callback: Callable):
        """注册任务状态变更回调"""
        with self._lock:
            self._callbacks[task_id] = callback

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """清理旧任务"""
        now = datetime.now()
        with self._lock:
            to_remove = []
            for task_id, task in self._tasks.items():
                if task.completed_at:
                    age = (now - task.completed_at).total_seconds() / 3600
                    if age > max_age_hours:
                        to_remove.append(task_id)
            
            for task_id in to_remove:
                self.remove_task(task_id)


class TaskCancelledError(Exception):
    """任务取消异常"""
    pass


# 全局单例
task_manager = TaskManager()
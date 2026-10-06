# app/tasks/celery_app.py - Celery 应用与队列路由（docs/design/01 §4.4 / 03 §7）
# M0 骨架：队列拓扑与路由冻结；具体任务模块随里程碑挂入 include：
#   parse_tasks(M3) · extract_tasks(M3) · graph_tasks(M4) · rdf_tasks(M4)
#
# worker 启动方式（对齐 docker-compose.app.yml）：
#   celery -A app.tasks.celery_app worker -Q parse   -n worker-parse@%h   -c 2
#   celery -A app.tasks.celery_app worker -Q extract -n worker-extract@%h -c 2
#   celery -A app.tasks.celery_app worker -Q graph   -n worker-graph@%h   -c 2
#   celery -A app.tasks.celery_app worker -Q rdf     -n worker-rdf@%h     -c 1   ← Oxigraph 唯一写者
#   celery -A app.tasks.celery_app beat

from celery import Celery
from kombu import Queue

from app.core.config import settings

celery_app = Celery(
    "ontology",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
    include=[
        "app.tasks.parse_tasks",    # M3-2：解析/切片（parse 队列）
        "app.tasks.graph_tasks",    # M3-3 行表回填 + M4 outbox→Neo4j 消费（graph 队列）
        "app.tasks.extract_tasks",  # M3-4：Schema/Instance 抽取（extract 队列）
        "app.tasks.rdf_tasks",      # M4：outbox→Oxigraph 命名图（rdf 队列，唯一写者）
    ],
)

celery_app.conf.update(
    # 队列拓扑（README §4.4）
    task_queues=(
        Queue("parse"),
        Queue("extract"),
        Queue("graph"),
        Queue("rdf"),
    ),
    task_routes={
        "app.tasks.parse_tasks.*": {"queue": "parse"},
        "app.tasks.extract_tasks.*": {"queue": "extract"},
        "app.tasks.graph_tasks.*": {"queue": "graph"},
        "app.tasks.rdf_tasks.*": {"queue": "rdf"},
    },
    task_default_queue="graph",
    # 可靠性：API/worker 重启任务不丢（01 §9 验收指标）
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    # 结果与序列化
    result_expires=86400,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    # beat：定时任务（08 §2 M3/M4）
    beat_schedule={
        # M4 outbox 消费链（02 §5）：worker-graph → Neo4j / worker-rdf → Oxigraph
        "drain-outbox-neo4j": {"task": "app.tasks.graph_tasks.drain_outbox",
                               "schedule": 30.0, "options": {"queue": "graph"}},
        "drain-outbox-rdf": {"task": "app.tasks.rdf_tasks.drain_rdf_outbox",
                             "schedule": 60.0, "options": {"queue": "rdf"}},
    },
)

# LLM 密集任务的时间上限（extract 队列；秒）
celery_app.conf.task_soft_time_limit = 3600
celery_app.conf.task_time_limit = 3900

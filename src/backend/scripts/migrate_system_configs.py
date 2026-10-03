# scripts/migrate_system_configs.py - M2 一次性导入（docs/design/03 §5 / 08 §2 M2）
# 将旧 system_configs.llm_config / vl_config 的模型配置迁移到 model_configs 表（api_key AES 加密），
# 非模型杂项（milvus_*/neo4j_*/vl_enabled 等）拆分到 system_configs.platform_config。
#
# 用法（在 src/backend 下）：
#   ../../.venv/Scripts/python scripts/migrate_system_configs.py
# 幂等：按 name 已存在则跳过；--force 覆盖更新。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.security import encrypt_str  # noqa: E402
from app.infrastructure.database import (  # noqa: E402
    ModelConfig,
    SessionLocal,
    SystemConfig,
    User,
)

LLM_CFG_KEY = "llm_config"
VL_CFG_KEY = "vl_config"
PLATFORM_CFG_KEY = "platform_config"

PLATFORM_KEYS = [  # llm_config 中非模型字段 → platform_config
    "milvus_enabled", "milvus_host", "milvus_port", "neo4j_uri",
    "neo4j_username", "neo4j_password", "vl_enabled",
]


def _admin_id(db) -> int:
    admin = db.query(User).filter(User.role == "admin").first()
    if admin:
        return admin.id
    user = db.query(User).first()
    return user.id if user else 1


def _upsert(db, admin_id: int, *, purpose: str, name: str, provider: str,
            base_url: str, model_name: str, api_key: str, params: dict,
            dims: int | None, is_default: bool, force: bool) -> str:
    existing = db.query(ModelConfig).filter(
        ModelConfig.scope == "global", ModelConfig.purpose == purpose, ModelConfig.name == name
    ).first()
    if existing and not force:
        return f"skip(existing) {purpose}/{name}"
    if not existing:
        existing = ModelConfig(scope="global", purpose=purpose, name=name,
                               created_by=admin_id, enabled=True)
        db.add(existing)
    existing.provider = provider
    existing.base_url = base_url
    existing.model_name = model_name
    existing.api_key_encrypted = encrypt_str(api_key) if api_key else None
    existing.params = params
    existing.dims = dims
    existing.is_default = is_default
    return f"upsert {purpose}/{name} -> {model_name} @ {base_url}"


def main(force: bool = False) -> None:
    db = SessionLocal()
    try:
        admin_id = _admin_id(db)
        llm_row = db.query(SystemConfig).filter(SystemConfig.key == LLM_CFG_KEY).first()
        vl_row = db.query(SystemConfig).filter(SystemConfig.key == VL_CFG_KEY).first()
        llm = dict(llm_row.value or {}) if llm_row else {}
        vl = dict(vl_row.value or {}) if vl_row else {}

        if not llm and not vl:
            print("system_configs 中无 llm_config/vl_config，无可迁移数据")
            return

        # 1) extract + chat（同一家 provider，两个用途各一行，均设默认）
        actions = [
            _upsert(db, admin_id, purpose="extract",
                    name="导入-抽取模型", provider="openai_compatible",
                    base_url=llm.get("base_url", ""), model_name=llm.get("model", ""),
                    api_key=llm.get("api_key", ""),
                    params={"chunk_size": llm.get("chunk_size", 2000),
                            "chunk_overlap": llm.get("chunk_overlap", 15),
                            "request_interval": llm.get("request_interval", 2),
                            "llm_timeout": llm.get("llm_timeout", 300),
                            "disable_think": llm.get("disable_think", False),
                            "streaming_enabled": llm.get("streaming_enabled", False)},
                    dims=None, is_default=True, force=force),
            _upsert(db, admin_id, purpose="chat",
                    name="导入-对话模型", provider="openai_compatible",
                    base_url=llm.get("base_url", ""), model_name=llm.get("model", ""),
                    api_key=llm.get("api_key", ""), params={}, dims=None,
                    is_default=True, force=force),
        ]
        # 2) embedding（Ollama 型，bge-m3=1024 维）
        if llm.get("embedding_base_url"):
            actions.append(_upsert(db, admin_id, purpose="embedding",
                                   name="导入-嵌入模型", provider="ollama",
                                   base_url=llm["embedding_base_url"],
                                   model_name=llm.get("embedding_model", "bge-m3:latest"),
                                   api_key=llm.get("embedding_api_key", ""),
                                   params={}, dims=1024, is_default=True, force=force))
        # 3) vl
        if vl.get("vl_base_url"):
            actions.append(_upsert(db, admin_id, purpose="vl",
                                   name="导入-视觉模型", provider="openai_compatible",
                                   base_url=vl["vl_base_url"], model_name=vl.get("vl_model", ""),
                                   api_key=vl.get("vl_api_key", ""),
                                   params={"disable_think": vl.get("vl_disable_think", False)},
                                   dims=None, is_default=True, force=force))

        # 4) 非模型杂项 → system_configs.platform_config
        platform = {k: llm[k] for k in PLATFORM_KEYS if k in llm}
        if platform:
            row = db.query(SystemConfig).filter(SystemConfig.key == PLATFORM_CFG_KEY).first()
            if row and not force:
                print("platform_config 已存在，跳过（--force 覆盖）")
            elif row:
                row.value = platform
            else:
                db.add(SystemConfig(key=PLATFORM_CFG_KEY, value=platform))

        db.commit()
        for a in actions:
            print(" -", a)
        print(f"完成。platform_config 键：{sorted(platform)}")
    finally:
        db.close()


if __name__ == "__main__":
    main(force="--force" in sys.argv)

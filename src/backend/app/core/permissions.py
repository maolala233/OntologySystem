# app/core/permissions.py - 权限判定纯函数（docs/design/02 §3.1 / README §4.1）
# 判定优先级：role=admin 全通过 → user_module_grants.allowed=0 显式拒绝
#           → grants.allowed=1 通过 → modules.is_default_on 兜底
# 纯函数不触碰 DB/请求对象，供 deps.py 与契约测试复用。

from __future__ import annotations

# 项目角色等级（owner > editor > viewer）
PROJECT_ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


def resolve_user_modules(role: str, grants: dict[str, bool],
                         default_on: set[str], all_modules: list[str]) -> list[str]:
    """解析用户可见模块列表。

    role: "admin" | "user"
    grants: {module_code: allowed} —— 仅含显式授权行（allow/deny）
    default_on: modules.is_default_on=true 的模块码集合
    all_modules: 全部 14 个模块码
    """
    if role == "admin":
        return list(all_modules)
    return [m for m in all_modules if grants.get(m, m in default_on)]


def has_project_role(member_role: str | None, min_role: str) -> bool:
    """项目角色判定：member_role=None 表示非成员。"""
    if member_role is None:
        return False
    return PROJECT_ROLE_RANK.get(member_role, 0) >= PROJECT_ROLE_RANK.get(min_role, 99)

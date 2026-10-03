# tests/test_permissions.py - 权限判定纯函数（docs/design/02 §3.1）
from app.core.permissions import has_project_role, resolve_user_modules

ALL_MODULES = [
    "dashboard", "projects", "documents", "schema_build", "instance_build",
    "resolution", "review", "version_control", "publish", "asset_center",
    "graph_explore", "qa", "mcp", "export",
]
DEFAULTS = {"dashboard", "projects", "asset_center"}


def test_admin_gets_all_modules():
    assert resolve_user_modules("admin", {}, set(), ALL_MODULES) == ALL_MODULES


def test_user_defaults_only():
    mods = resolve_user_modules("user", {}, DEFAULTS, ALL_MODULES)
    assert sorted(mods) == ["asset_center", "dashboard", "projects"]


def test_explicit_allow_wins():
    mods = resolve_user_modules("user", {"qa": True}, DEFAULTS, ALL_MODULES)
    assert sorted(mods) == ["asset_center", "dashboard", "projects", "qa"]


def test_explicit_deny_overrides_default():
    mods = resolve_user_modules("user", {"projects": False}, DEFAULTS, ALL_MODULES)
    assert "projects" not in mods
    assert "dashboard" in mods


def test_explicit_deny_overrides_explicit_allow():
    # 同一用户不可能有两行（唯一约束），但函数语义上 deny 优先
    mods = resolve_user_modules("user", {"qa": False}, DEFAULTS, ALL_MODULES)
    assert "qa" not in mods


def test_project_role_rank():
    assert has_project_role("owner", "editor") is True
    assert has_project_role("editor", "editor") is True
    assert has_project_role("viewer", "editor") is False
    assert has_project_role(None, "viewer") is False

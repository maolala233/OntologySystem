from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.infrastructure.database import get_db, User, Module, UserModuleGrant
from jose import JWTError, jwt
from datetime import datetime, timedelta
from app.core.config import settings
from app.core.deps import require_role
from pydantic import BaseModel
from typing import Optional, List
import bcrypt
import uuid


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))

router = APIRouter(prefix="/api/auth", tags=["auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Schemas
class UserRegister(BaseModel):
    username: str
    password: str

class UserResponse(BaseModel):
    id: int
    username: str

    class Config:
        from_attributes = True

class ChangePassword(BaseModel):
    old_password: str
    new_password: str

class ResetPassword(BaseModel):
    new_password: str

class UserListItem(BaseModel):
    id: int
    username: str
    is_active: bool

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    user: UserResponse

class RefreshRequest(BaseModel):
    refresh_token: str

class UpdateMe(BaseModel):
    display_name: Optional[str] = None
    locale: Optional[str] = None


def _create_token(data: dict, token_type: str, expires_delta: timedelta) -> str:
    to_encode = data.copy()
    to_encode.update({"type": token_type, "exp": datetime.utcnow() + expires_delta})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(data: dict) -> str:
    return _create_token(data, "access", timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))


def create_refresh_token(data: dict) -> str:
    return _create_token(data, "refresh", timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))


def verify_token(token: str, db: Session) -> User:
    """
    验证 JWT token 并返回用户对象
    用于不支持 headers 的场景（如 EventSource/SSE）
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
    )
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    return user


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """M1：委托 core.deps 统一实现（含 type=refresh 拒绝、过期区分、is_active 校验）。"""
    from app.core.deps import _load_user_or_raise

    return _load_user_or_raise(token, db)


def _user_modules(db: Session, user: User) -> List[str]:
    """用户可见模块列表（判定逻辑与 core/deps.require_module 一致）。"""
    from app.core.permissions import resolve_user_modules

    if user.role == "admin":
        modules = db.query(Module).order_by(Module.sort_order).all()
        return [m.code for m in modules]
    grants_rows = db.query(UserModuleGrant).filter(UserModuleGrant.user_id == user.id).all()
    grants = {g.module_code: bool(g.allowed) for g in grants_rows}
    modules = db.query(Module).order_by(Module.sort_order).all()
    return resolve_user_modules(
        "user", grants, {m.code for m in modules if m.is_default_on}, [m.code for m in modules]
    )


@router.post("/register", response_model=Token)
def register(user_data: UserRegister, db: Session = Depends(get_db)):
    # 检查用户名是否已存在
    existing_user = db.query(User).filter(User.username == user_data.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already registered")

    new_user = User(
        username=user_data.username,
        hashed_password=hash_password(user_data.password)
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # M1：注册即记录登录时间；模块按 is_default_on 动态解析，无需写授权行
    new_user.last_login_at = datetime.utcnow()
    db.commit()

    return {
        "access_token": create_access_token(data={"sub": new_user.username}),
        "refresh_token": create_refresh_token(data={"sub": new_user.username}),
        "token_type": "bearer",
        "user": UserResponse.model_validate(new_user)
    }

@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect username or password")

    user.last_login_at = datetime.utcnow()
    db.commit()

    return {
        "access_token": create_access_token(data={"sub": user.username}),
        "refresh_token": create_refresh_token(data={"sub": user.username}),
        "token_type": "bearer",
        "user": UserResponse.model_validate(user)
    }


@router.post("/refresh", response_model=Token)
def refresh(data: RefreshRequest, db: Session = Depends(get_db)):
    """M1：用 refresh token 换发新令牌对（03 §3）。"""
    credentials_exception = HTTPException(status_code=401, detail="Invalid refresh token")
    try:
        payload = jwt.decode(data.refresh_token, settings.JWT_SECRET_KEY,
                             algorithms=[settings.ALGORITHM])
    except JWTError:
        raise credentials_exception
    if payload.get("type") != "refresh":
        raise credentials_exception
    username = payload.get("sub")
    user = db.query(User).filter(User.username == username).first() if username else None
    if user is None or not user.is_active:
        raise credentials_exception
    return {
        "access_token": create_access_token(data={"sub": user.username}),
        "refresh_token": create_refresh_token(data={"sub": user.username}),
        "token_type": "bearer",
        "user": UserResponse.model_validate(user)
    }


class UserMeResponse(BaseModel):
    """M1：/auth/me 扩展响应（旧字段 id/username 保留，向后兼容）。"""
    id: int
    username: str
    role: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    locale: str = "zh-CN"
    is_active: bool = True
    modules: List[str] = []


@router.get("/me", response_model=UserMeResponse)
def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return UserMeResponse(
        id=current_user.id,
        username=current_user.username,
        role=current_user.role,
        display_name=current_user.display_name,
        email=current_user.email,
        locale=current_user.locale,
        is_active=current_user.is_active,
        modules=_user_modules(db, current_user),
    )


@router.put("/me")
def update_me(data: UpdateMe, current_user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """M1：改 display_name / locale（密码修改沿用 /change-password）。"""
    if data.display_name is not None:
        current_user.display_name = data.display_name.strip() or None
    if data.locale is not None:
        if data.locale not in ("zh-CN", "en"):
            raise HTTPException(status_code=400, detail="不支持的语言")
        current_user.locale = data.locale
    db.commit()
    return {"message": "ok"}


@router.put("/change-password")
def change_password(data: ChangePassword, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(data.old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="旧密码错误")
    current_user.hashed_password = hash_password(data.new_password)
    db.commit()
    return {"message": "密码修改成功"}


@router.get("/sse-ticket")
def issue_sse_ticket(task_id: str = "", current_user: User = Depends(get_current_user)):
    """M1：签发一次性 SSE ticket（60s TTL，Redis），替代 ?token= 明文长令牌（03 §3）。"""
    import json

    import redis

    from app.core.config import settings

    ticket = uuid.uuid4().hex
    payload = {"user_id": current_user.id, "task_id": task_id}
    from app.services.env_config_service import redis_url
    client = redis.Redis.from_url(redis_url(), socket_connect_timeout=2)
    client.setex(f"sse:ticket:{ticket}", 60, json.dumps(payload))
    return {"ticket": ticket, "expires_in": 60}


@router.get("/users", response_model=List[UserListItem])
def get_users(
    _admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    users = db.query(User).all()
    return [UserListItem.model_validate(u) for u in users]


@router.put("/users/{user_id}/reset-password")
def reset_password(user_id: int, data: ResetPassword,
                   _admin: User = Depends(require_role("admin")),
                   db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.hashed_password = hash_password(data.new_password)
    db.commit()
    return {"message": "密码重置成功"}

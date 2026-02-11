from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any, List
from app.infrastructure.database import get_db, SystemConfig, User
from app.schemas.ontology import SystemConfigUpdate, SystemConfigResponse
from app.api.auth import get_current_user

router = APIRouter(prefix="/api/system", tags=["system"])

@router.get("/config/{key}", response_model=SystemConfigResponse)
def get_config(key: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if not config:
        # Return a default empty config if not found
        return {
            "id": 0,
            "key": key,
            "value": {},
            "updated_at": "2024-01-01T00:00:00"
        }
    return config

@router.put("/config/{key}", response_model=SystemConfigResponse)
def update_config(
    key: str, 
    config_update: SystemConfigUpdate, 
    current_user: User = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    # Authorization: Only 'admin' can change system settings
    if current_user.username != "admin":
        raise HTTPException(status_code=403, detail="Only admin can modify system configuration")
    
    db_config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if db_config:
        db_config.value = config_update.value
    else:
        db_config = SystemConfig(key=key, value=config_update.value)
        db.add(db_config)
    
    db.commit()
    db.refresh(db_config)
    return db_config

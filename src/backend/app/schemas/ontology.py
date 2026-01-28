from pydantic import BaseModel
from typing import Optional, Dict, List, Any
from datetime import datetime

class ProjectBase(BaseModel):
    name: str
    description: Optional[str] = None

class ProjectCreate(ProjectBase):
    pass

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    graph_data: Optional[Dict[str, Any]] = None
    is_published: Optional[bool] = None

class ProjectResponse(ProjectBase):
    id: int
    owner_id: int
    graph_data: Optional[Dict[str, Any]] = None
    ttl_content: Optional[str] = None
    is_published: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
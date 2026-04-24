from pydantic import BaseModel

# 공통 필드
class TaskBase(BaseModel):
    content: str
    is_completed: bool = False

# 생성 시 사용
class TaskCreate(TaskBase):
    pass

# 업데이트 시 사용
class TaskUpdate(BaseModel):
    content: str | None = None
    is_completed: bool | None = None

# 응답 시 사용
class TaskResponse(TaskBase):
    id: int

    class Config:
        from_attributes = True  # ORM 모델과 호환되도록 설정
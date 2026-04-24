from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware

# FastAPI 앱 인스턴스 생성
app = FastAPI()

# --- CORS 설정 ---
# 실제 운영 환경에서는 ["http://localhost:3000"] 처럼 특정 도메인만 허용하는 것이 보안상 좋습니다.
# 여기서는 모든 도메인의 요청을 허용하여 개발 편의성을 높였습니다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 데이터 모델 정의 ---
class TodoItem(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    completed: bool = False

# --- 메모리 데이터베이스 (간단한 리스트) ---
todos_db: List[TodoItem] = []
next_id = 1

# --- 엔드포인트 구현 ---

# 1. GET /api/todos: 모든 할 일 목록 조회
@app.get("/api/todos", response_model=List[TodoItem])
def get_todos():
    return todos_db

# 2. POST /api/todos: 새 할 일 생성
@app.post("/api/todos", response_model=TodoItem, status_code=201)
def create_todo(todo: TodoItem):
    global next_id
    # 새 아이템 생성 (ID 자동 증가)
    new_item = TodoItem(id=next_id, **todo.dict())
    todos_db.append(new_item)
    next_id += 1
    return new_item

# 3. PUT /api/todos/{todo_id}: 할 일 수정
@app.put("/api/todos/{todo_id}", response_model=TodoItem)
def update_todo(todo_id: int, updated_todo: TodoItem):
    for i, todo in enumerate(todos_db):
        if todo.id == todo_id:
            # 기존 데이터에 ID를 유지하면서 업데이트
            updated_item = TodoItem(id=todo_id, **updated_todo.dict())
            todos_db[i] = updated_item
            return updated_item
    
    # ID가 존재하지 않을 경우 404 에러 반환
    raise HTTPException(status_code=404, detail="Todo not found")

# 4. DELETE /api/todos/{todo_id}: 할 일 삭제
@app.delete("/api/todos/{todo_id}")
def delete_todo(todo_id: int):
    for i, todo in enumerate(todos_db):
        if todo.id == todo_id:
            deleted_item = todos_db.pop(i)
            return {"message": "Deleted successfully", "item": deleted_item}
    
    raise HTTPException(status_code=404, detail="Todo not found")

# 서버 실행 확인용 루트 엔드포인트
@app.get("/")
def read_root():
    return {"message": "FastAPI TODO Server is running"}
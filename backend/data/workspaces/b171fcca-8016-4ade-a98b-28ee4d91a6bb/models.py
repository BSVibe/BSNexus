from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 데이터베이스 설정
SQLALCHEMY_DATABASE_URL = "sqlite:///./todo.db"

# SQLite 엔진 생성 (check_same_thread=False는 SQLite와 Thread-safe한 동시성을 위해 필요합니다)
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base 클래스 생성
Base = declarative_base()

# Task 모델 정의
class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    content = Column(String, nullable=False)
    is_completed = Column(Boolean, default=False)

# 데이터베이스 테이블 생성 함수 (프로젝트 시작 시 실행 필요)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
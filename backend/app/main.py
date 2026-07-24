from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.model import user

from app.api import auth
from app.database import Base, engine, SessionLocal
Base.metadata.create_all(engine)
app = FastAPI(
    title="LinguaAI",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://languaai.madcamp-kaist.org",
        "http://localhost:5173",  # 프론트엔드 로컬 개발 서버(Vite)
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)

@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/items/{item_id}")
def read_item(item_id: int, q: str = None):
    return {"item_id": item_id, "q": q}
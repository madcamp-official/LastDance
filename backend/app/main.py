from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.model import user
from app.model import problem
from app.model import session as session_model
from app.model import analysis as analysis_model
from app.model import ingest as ingest_model
from app.model import feedback as feedback_model

from app.api import auth
from app.api import problem as problem_api
from app.api import session as session_api
from app.api import analysis as analysis_api
from app.api import ingest as ingest_api
from app.api import feedback as feedback_api
from app.database import Base, engine, SessionLocal
from app.util.messaging import start_producer, stop_producer, close_redis
from app.worker.consumer import start_consumer, stop_consumer
Base.metadata.create_all(engine)
app = FastAPI(
    title="LinguaAI",
    version="0.1.0",
)

auth.install_error_handler(app)


@app.on_event("startup")
async def _start_ingest_infra() -> None:
    # Ingest Gateway(§3)의 Kafka producer + Replay Worker(Kafka consumer) 기동
    await start_producer()
    start_consumer()


@app.on_event("shutdown")
async def _stop_ingest_infra() -> None:
    await stop_consumer()
    await stop_producer()
    await close_redis()

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
app.include_router(problem_api.router)
app.include_router(session_api.router)
app.include_router(analysis_api.router)
app.include_router(ingest_api.router)
app.include_router(feedback_api.router)

@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/items/{item_id}")
def read_item(item_id: int, q: str = None):
    return {"item_id": item_id, "q": q}
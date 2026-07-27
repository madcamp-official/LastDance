import os
from typing import Optional

from aiokafka import AIOKafkaProducer
from redis.asyncio import Redis

import app.database  # noqa: F401  # 임포트 시점에 .env 로드(app/database.py의 _load_env) 트리거

# dev-plan §3 Ingest Gateway 인프라. Redis=last_seq 중복 제거(§3.1), Kafka=이벤트 로그(§1 Event Log).
# S3 Raw Blob Store는 이번 범위에서 로컬 VM 디스크 저장으로 대체(app/worker/rawstore.py 참고).
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "keystroke-events")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
LAST_SEQ_TTL_SECONDS = 24 * 60 * 60  # dev-plan §3.1: last_seq는 세션별 TTL 24h

_redis: Optional[Redis] = None
_producer: Optional[AIOKafkaProducer] = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def start_producer() -> AIOKafkaProducer:
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)
        await _producer.start()
    return _producer


async def stop_producer() -> None:
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None


def get_producer() -> AIOKafkaProducer:
    if _producer is None:
        raise RuntimeError("Kafka producer가 초기화되지 않았습니다 (앱 startup 순서 확인)")
    return _producer

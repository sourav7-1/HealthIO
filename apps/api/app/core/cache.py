from redis.asyncio import Redis

from app.core.config import Settings


def create_redis(settings: Settings) -> Redis:
    client: Redis = Redis.from_url(str(settings.redis_url), decode_responses=True)
    return client

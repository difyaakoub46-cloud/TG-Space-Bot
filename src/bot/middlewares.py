import asyncio
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.exceptions import TelegramRetryAfter
from loguru import logger


class RateLimitMiddleware(BaseMiddleware):
    """
    Middleware to gracefully handle Telegram Rate Limits and flood wait exceptions.
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramRetryAfter as e:
            logger.warning(f"Rate limited by Telegram API! Sleeping for {e.retry_after} seconds.")
            await asyncio.sleep(e.retry_after)
            return await handler(event, data)
        except Exception as e:
            logger.error(f"Unhandled error in middleware pipeline: {e}")
            raise e

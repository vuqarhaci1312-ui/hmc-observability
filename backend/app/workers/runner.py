import asyncio
import logging

from app.workers.consume import drain_once
from app.workers.http_probe import serve

logger = logging.getLogger(__name__)


async def loop() -> None:
    serve(8001)
    while True:
        try:
            count = await drain_once("worker")
            if count == 0:
                await asyncio.sleep(0.5)
        except Exception:
            logger.exception("worker iteration failed")
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(loop())

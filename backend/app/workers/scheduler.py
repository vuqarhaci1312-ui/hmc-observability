import asyncio
import logging

from app.simulation.live import evaluate_live, simulation_tick
from app.workers.http_probe import serve

logger = logging.getLogger(__name__)


async def loop() -> None:
    serve(8002)
    tick = 0
    while True:
        try:
            await simulation_tick()
            if tick % 3 == 0:
                await evaluate_live()
        except Exception:
            logger.exception("scheduler iteration failed")
        tick += 1
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(loop())

import asyncio
import os

from sqlalchemy import select

from app.database.session import get_session_factory
from app.models.entities import SimulationJob
from app.simulation.seed import resume_history, seed_demo


async def main() -> None:
    profile = os.environ.get("DEMO_PROFILE", "quick")
    seed = int(os.environ.get("DEMO_RANDOM_SEED", "42"))
    factory = get_session_factory()
    async with factory() as session:
        job = await session.scalar(
            select(SimulationJob).where(SimulationJob.profile_name == profile).order_by(SimulationJob.started_at.desc())
        )
    if job is None:
        print(await seed_demo(profile, seed), flush=True)
        return
    if job.status == "seeding":
        try:
            print(await resume_history(profile, seed), flush=True)
        except RuntimeError:
            print(await seed_demo(profile, seed, reseed=True), flush=True)
        return
    print({"status": job.status, "seed_id": job.seed_id, "message": "already seeded"}, flush=True)


if __name__ == "__main__":
    asyncio.run(main())

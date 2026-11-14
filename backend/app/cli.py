import argparse
import asyncio
import os

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(prog="hmc")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed")
    seed.add_argument("--profile", default=os.getenv("DEMO_PROFILE", "quick"))
    seed.add_argument("--seed", type=int, default=int(os.getenv("DEMO_RANDOM_SEED", "42")))
    seed.add_argument("--reseed", action="store_true")
    seed.add_argument("--resume", action="store_true")
    user = sub.add_parser("create-user")
    user.add_argument("--email", required=True)
    user.add_argument("--password", required=True)
    user.add_argument("--name", default="HMC Operator")
    user.add_argument("--role", default="super_admin")
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if args.command == "seed":
        if args.resume:
            from app.simulation.seed import resume_history
            print(asyncio.run(resume_history(args.profile, args.seed)))
        else:
            from app.simulation.seed import seed_demo
            print(asyncio.run(seed_demo(args.profile, args.seed, reseed=args.reseed)))
    elif args.command == "create-user":
        asyncio.run(_create_user(args.email, args.password, args.name, args.role))
    elif args.command == "smoke":
        asyncio.run(_smoke(args.base_url))


async def _create_user(email: str, password: str, name: str, role: str) -> None:
    from app.core.security import hash_password
    from app.database.session import get_session_factory
    from app.models.entities import User
    factory = get_session_factory()
    async with factory() as session:
        session.add(User(email=email, full_name=name, password_hash=hash_password(password), role=role, is_active=True))
        await session.commit()
    print({"email": email, "role": role})


async def _smoke(base_url: str) -> None:
    email = os.environ["DEMO_ADMIN_EMAIL"]
    password = os.environ["DEMO_ADMIN_PASSWORD"]
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        live = await client.get("/health/live")
        live.raise_for_status()
        login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        tenants = await client.get("/api/v1/tenants", headers=headers)
        tenants.raise_for_status()
        summary = await client.get("/api/v1/overview/summary", headers=headers)
        summary.raise_for_status()
        print({"live": live.json(), "tenants": len(tenants.json().get("items", [])), "summary": summary.json()})


if __name__ == "__main__":
    main()

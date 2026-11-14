import os

os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-123")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://hmc:hmc@localhost:5432/hmc")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

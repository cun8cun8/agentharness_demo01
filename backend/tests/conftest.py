import os


os.environ.setdefault("RESEARCHFORGE_PERSISTENCE", "0")
os.environ.setdefault("RESEARCHFORGE_JOB_DELIVERY_MODE", "list")
os.environ.setdefault("RESEARCHFORGE_RATE_LIMIT_BACKEND", "local")
os.environ.setdefault("RESEARCHFORGE_RATE_LIMIT_PER_MINUTE", "100000")
os.environ.setdefault("RESEARCHFORGE_AUTH_RATE_LIMIT_PER_MINUTE", "100000")

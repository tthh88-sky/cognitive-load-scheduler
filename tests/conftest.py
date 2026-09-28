import os
import tempfile

_tmp_dir = tempfile.mkdtemp(prefix="scheduler-tests-")
os.environ["SCHEDULER_DB_URL"] = f"sqlite:///{_tmp_dir}/test_scheduler.db"
os.environ.setdefault("LLM_PROVIDER", "none")

import pytest  # noqa: E402


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app import models
    from app.database import Base, engine
    from app.main import app

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with TestClient(app) as c:
        yield c

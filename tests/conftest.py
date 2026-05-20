import pytest
from unittest.mock import patch
from starlette.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    # Prevent the background model-loading thread from attempting real connections.
    with patch("main.load_models"):
        import main
        with TestClient(main.app, raise_server_exceptions=True) as c:
            yield c

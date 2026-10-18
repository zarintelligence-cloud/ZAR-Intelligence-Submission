import base64, os
import pytest

os.environ.setdefault("ZAR_LLM_MODE", "mock")
os.environ.setdefault("ZAR_STORE_MODE", "memory")
os.environ.setdefault("ZAR_ASSET_MODE", "memory")
os.environ.setdefault("ZAR_INVOKE_MODE", "local")
os.environ.setdefault("ZAR_DOMAINS", "retail,saas,logistics")
os.environ.setdefault("ZAR_DEMO_SEED", "0")


@pytest.fixture
def app():
    from zar_core.bootstrap import build
    return build("monolith", domains=["retail", "saas", "logistics"], invoke_mode="local")

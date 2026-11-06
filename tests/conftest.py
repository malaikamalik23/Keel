import pytest

from app.config import Settings
from app.container import build_container
from tests.fakes import ScriptedLLM


def make_settings(**kw) -> Settings:
    kw.setdefault("anthropic_api_key", None)
    return Settings(database_url="sqlite://", _env_file=None, **kw)


@pytest.fixture
def make_container():
    def _make(llm=None, **settings_kw):
        return build_container(make_settings(**settings_kw), llm=llm or ScriptedLLM([]))

    return _make

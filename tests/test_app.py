import os
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


def test_app_runs_and_handles_an_emergency_without_any_llm(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    os.environ.pop("LLM_BASE_URL", None)
    at = st_testing.AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    next(b for b in at.button if b.label == "Emergency").click().run()
    assert not at.exception
    assert "112" in at.chat_message[0].markdown[0].value  # newest message first
    assert any("Emergency flagged" in e.value for e in at.error)

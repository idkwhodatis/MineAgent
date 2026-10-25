"""Stage-zero guards: product migration must not silently rewrite old evidence."""

import hashlib
import json
import secrets
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings
from packages.tradegrad_adapter.config import OptimizerConfig

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_contract_fixtures_and_migrations_remain_byte_identical():
    manifest = json.loads((ROOT / "compatibility/legacy_contracts.json").read_text())
    assert manifest["baseline_commit"] == "6828f3bd429fc754648021bae703a14fa300b62e"
    assert len(manifest["immutable_files"]) >= 10
    for name, digest in manifest["immutable_files"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name


def test_new_capabilities_default_off_without_changing_legacy_settings(monkeypatch):
    names = (
        "factor_library_enabled",
        "rdagent_research_enabled",
        "openevolve_research_enabled",
    )
    for name in names:
        monkeypatch.delenv(name.upper(), raising=False)
    settings = Settings(
        database_url="postgresql://localhost/unused",
        secret_key=secrets.token_urlsafe(48),
    )
    assert all(getattr(settings, name) is False for name in names)
    assert settings.max_active_runs_per_user == 4
    assert settings.model_profiles_enabled is False


@pytest.mark.parametrize("new_engine", ["openevolve", "rdagent", "mineagent"])
def test_new_engines_cannot_be_smuggled_into_legacy_optimizer(new_engine):
    with pytest.raises(ValidationError):
        OptimizerConfig(algorithm=new_engine)
    assert OptimizerConfig().algorithm == "trade_tgd"


def test_visible_product_label_is_not_a_fabricated_run_status():
    layout = (ROOT / "frontend/src/components/layout.tsx").read_text()
    assert 'aria-label="MineAgent overview"' in layout
    assert "https://github.com/idkwhodatis/MineAgent" in layout
    assert "Seed evaluation</span>" not in layout

"""Startup fails clearly when a Foundry service-principal variable is missing."""
import pytest

import ai


@pytest.mark.parametrize("name", ai.FOUNDRY_AUTH_VARS)
def test_startup_names_the_missing_service_principal_variable(monkeypatch, name):
    monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match=name):
        ai.require_foundry_config()


def test_startup_rejects_a_blank_service_principal_variable(monkeypatch):
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "   ")
    with pytest.raises(RuntimeError, match="AZURE_CLIENT_SECRET"):
        ai.require_foundry_config()

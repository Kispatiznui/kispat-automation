import copy

import pytest
from pydantic import ValidationError

from shared.contracts.manifest import AutomationManifest
from tests.conftest import example_manifest_dict


def _mutate(path: list, value):
    data = copy.deepcopy(example_manifest_dict())
    cur = data
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = value
    return data


def test_hello_world_valid():
    m = AutomationManifest.model_validate(example_manifest_dict())
    assert m.metadata.name == "hello-world"
    assert m.spec.permissions.network is False


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (["spec", "entrypoint"], "../evil.py"),
        (["spec", "entrypoint"], "/etc/passwd"),
        (["spec", "permissions", "secrets"], ["API_KEY=abc123"]),
        (["spec", "limits", "memory_mb"], 16),
        (["spec", "limits", "memory_mb"], 8192),
        (["spec", "limits", "cpu"], 8),
        (["spec", "triggers"], []),
        (["metadata", "version"], "1.0"),
        (["spec", "env"], {"DB_PASSWORD": "x"}),
        (["spec", "runtime"], "ruby"),
        (["spec", "unknown_field"], 1),
        (["metadata", "name"], "Nombre Inválido"),
    ],
)
def test_invalid(path, value):
    with pytest.raises(ValidationError):
        AutomationManifest.model_validate(_mutate(path, value))


def test_cron_requires_schedule():
    with pytest.raises(ValidationError):
        AutomationManifest.model_validate(_mutate(["spec", "triggers"], [{"type": "cron"}]))

"""In-memory test fixture: no VM, network, or actual commands."""
from __future__ import annotations

from workspaces.provisioner import SessionSpec


class FixtureProvisioner:
    backend_name = "test_fixture"
    execution_enabled = True

    def __init__(self):
        self.calls = []

    async def provision_session(self, spec: SessionSpec) -> str:
        spec.validate()
        self.calls.append(("create", spec.session_id))
        return "fixture://" + spec.session_id

    async def terminate_session(self, session_id: str, backend_ref: str) -> None:
        assert backend_ref == "fixture://" + session_id
        self.calls.append(("stop", session_id))

    async def execute_in_session(self, session_id, backend_ref, command, *, timeout_seconds=10):
        assert backend_ref == "fixture://" + session_id
        return {"session_id": session_id, "result": {"exitcode": 0}}

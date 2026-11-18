"""Read-only upgrade assessment; never mutates a container or persistent data.

An add-on image update is NOT a safe database migration. This module draws
explicit distinctions among first install, in-place identity, and upgrades,
and keeps stateful changes blocked until backup and migration orchestration
exists. Operator approval is necessary but not sufficient for stateful upgrades.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import ApprovedDeployment


class UpgradePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["replace_container"] = "replace_container"
    data_migration: Literal["none", "compatible", "requires_migration"] = "none"
    backup_required: bool = True
    rollback_supported: bool = False
    instructions: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def conservative(self) -> UpgradePolicy:
        if self.data_migration == "requires_migration" and not self.backup_required:
            raise ValueError("Data migrations may not bypass backups")
        return self


def assess_upgrade(
    desired: dict | None,
    observed_state: str,
    candidate: ApprovedDeployment,
    policy: UpgradePolicy,
) -> dict:
    """Return requirements and blockers; never claim a backup was performed."""
    checks: list[dict[str, str]] = []

    def check(name: str, status: str, explanation: str) -> None:
        checks.append({"name": name, "status": status, "explanation": explanation})

    previous = desired if desired and desired.get("desired_state") == "running" else None
    if previous is None:
        check("existing_release", "blocked", "No verified running release is recorded")
    else:
        check("existing_release", "ready", "Previous release identity is recorded")

    if observed_state == "running":
        check("observed_state", "ready", "Existing managed container reports running")
    elif observed_state == "unmanaged_container_collision":
        check("observed_state", "blocked", "Unowned container collision must be resolved")
    else:
        check("observed_state", "blocked", "Live state is not verified as running")

    if previous is not None:
        old_version = str(previous["version"])
        old_image = str(previous["image"])
        if old_image == candidate.image and old_version == candidate.version:
            check("target_identity", "blocked", "Identical image and version; no upgrade")
        elif old_image == candidate.image or old_version == candidate.version:
            check(
                "target_identity", "blocked",
                "Release version and immutable image must both change",
            )
        else:
            check("target_identity", "ready", "Version and immutable image differ")

    if candidate.data_volume:
        check(
            "persistent_data", "blocked",
            "Data-volume upgrades require an independently verified backup and "
            "migration/rollback procedure; container replacement alone is insufficient",
        )
    else:
        check(
            "persistent_data", "ready",
            "No persistent add-on data volume is requested; application must still "
            "document any external state",
        )

    if policy.data_migration == "requires_migration":
        check(
            "migration", "blocked",
            "Migration runner and tested compensating recovery are not implemented",
        )
    elif policy.data_migration == "compatible":
        check(
            "migration", "blocked",
            "Compatibility claim requires an application-level acceptance check",
        )
    else:
        check("migration", "ready", "No data migration declared")

    check(
        "rollback", "blocked",
        "Verified staged rollback, previous-image preservation and durable cutover "
        "are not implemented in the current controller",
    )

    return {
        "addon_id": candidate.addon_id,
        "from": {
            "version": previous.get("version") if previous else None,
            "image": previous.get("image") if previous else None,
        },
        "to": {"version": candidate.version, "image": candidate.image},
        "strategy": policy.strategy,
        "data_migration": policy.data_migration,
        "backup_required": policy.backup_required,
        "rollback_supported": False,
        "checks": checks,
        "can_execute": False,
        "reason": (
            "Upgrade execution is deliberately disabled until durable backup, "
            "staged rollout and recovery mechanisms are verified"
        ),
    }

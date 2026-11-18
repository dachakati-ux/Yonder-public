"""Worker registration trust policy helpers."""

from __future__ import annotations

from typing import Any

from yonder_configuration.models import WorkerRegistrationPolicyConfig


def evaluate_worker_registration(
    policy: WorkerRegistrationPolicyConfig,
    worker_id: str,
) -> dict[str, Any]:
    """Return trust decision for a registering worker.

    This is a stub policy until production trust (mTLS / allowlists / attestation)
    is defined. It never invents worker identity — it only classifies.
    """

    if policy.mode == "allow_all":
        return {
            "trusted": True,
            "trust_state": "trusted",
            "registration_mode": "self_registration_allow_all",
            "accepted": True,
            "reason": "policy_allow_all",
        }

    allowed = set(policy.allowed_worker_ids)
    if worker_id in allowed:
        return {
            "trusted": True,
            "trust_state": "trusted",
            "registration_mode": "self_registration_allowlist",
            "accepted": True,
            "reason": "worker_id_allowlisted",
        }

    if policy.accept_untrusted:
        return {
            "trusted": False,
            "trust_state": "untrusted",
            "registration_mode": "self_registration_pending_trust",
            "accepted": True,
            "reason": "worker_id_not_allowlisted",
        }

    return {
        "trusted": False,
        "trust_state": "rejected",
        "registration_mode": "self_registration_rejected",
        "accepted": False,
        "reason": "worker_id_not_allowlisted",
    }

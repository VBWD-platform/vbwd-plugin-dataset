"""Dataset grace-period scheduler — revokes expired access (T7).

Mirrors ghrm's ``src/scheduler.py``: a module-level function a cron/CLI driver
calls to revoke every grace-expired ``DatasetMembership``. Kept independent of a
running background thread so it is safe to call from tests and from the pre-commit
gate. Wiring into a live cron is a DevOps concern (the same posture as ghrm's
scheduler).
"""
import logging

logger = logging.getLogger(__name__)


def revoke_expired_grace_access() -> int:
    """Revoke all grace-expired dataset memberships. Returns the count.

    Builds the access service through the plugin composition root (fresh
    ``db.session``) exactly like ghrm's scheduler. A failure is logged and
    swallowed so a scheduler tick never crashes the process.
    """
    try:
        from plugins.dataset import build_dataset_access_service

        count = build_dataset_access_service().revoke_expired_grace_access()
        if count:
            logger.info("[dataset] Revoked %d expired grace access records", count)
        return count
    except Exception as scheduler_error:  # noqa: BLE001 — a tick must never crash
        logger.error(
            "[dataset] Grace period scheduler error: %s",
            scheduler_error,
            exc_info=True,
        )
        return 0

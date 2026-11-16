"""Verified coordinator response shape, without downloads or private keys."""


def reviewed_discovery(**changes):
    document = {
        "pair_manifest": {"app": {"app_release_id": "app-next"}},
        "database_manifest": {"database_release_id": "db-next",
                              "released_at": "2026-11-16T00:00:00Z",
                              "database": {"game_version": "7.2"}},
        "artifacts": {"app": {"size_bytes": 12, "format": "zip"},
                      "database": {"size_bytes": 34, "format": "sqlite"}},
    }
    return {
        "status": "available", "source": "remote", "checked_at": "fixed",
        "authoritative": True, "can_offer_update": True, "reason": None,
        "release_pair_id": "pair-next", "database_release_id": "db-next",
        "confirmation_digest": "d" * 64, "document_sha256": "d" * 64,
        **document,
        "update_options": [{"mode": "both", "confirmation_digest": "d" * 64,
                            "reuse_app": False, "reuse_database": False, "document": document}],
        **changes,
    }

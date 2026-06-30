"""
Record edit authorization — PURE LOGIC (no DB, no Flask), so the entire access-control
matrix is unit-tested offline. The DB/HTTP layers are thin glue that supply the inputs.

Locked after adversarial security review (2026-06-30):
  * Principal = the SERVER-STAMPED Authentik username. ORCID/contributors in the record
    body are CLIENT-CONTROLLED and confer NO rights (they'd be a trivial self-grant).
  * Edit rights = admin OR owner (`attribution.uploaded_by == caller`) OR an explicit
    `record_acl` editor grant (by username). Default deny. Unowned legacy records are
    admin-only (never an unowned free-for-all).
  * Only the owner or an admin may MANAGE the ACL — an editor cannot re-grant (no
    privilege delegation). The owner is never an ACL row.
"""
from __future__ import annotations

ADMIN_GROUPS = {"admin"}
EDIT_ROLES = {"editor"}


def is_admin(groups) -> bool:
    return bool(groups) and any(g in ADMIN_GROUPS for g in groups)


def record_owner(record: dict) -> str | None:
    """The server-stamped submitter, or None for legacy/unowned records."""
    return ((record or {}).get("attribution") or {}).get("uploaded_by")


def can_edit_record(record: dict, caller: str | None, caller_is_admin: bool,
                    acl_editors=None) -> tuple[bool, str]:
    """Authoritative edit check. Returns (allowed, reason).

    acl_editors: iterable of usernames that hold an explicit 'editor' grant on the record.
    """
    if caller_is_admin:
        return True, "admin"
    if not caller:
        return False, "unauthenticated"
    owner = record_owner(record)
    if owner is None:
        # Unowned legacy record: only an admin may edit (or first assign an owner).
        return False, "unowned_admin_only"
    if caller == owner:
        return True, "owner"
    if acl_editors and caller in set(acl_editors):
        return True, "acl_editor"
    return False, "forbidden"


def can_manage_acl(record: dict, caller: str | None, caller_is_admin: bool) -> bool:
    """Who may grant/revoke collaborators: ONLY the owner or an admin. Editors cannot
    re-grant (closes the editor-escalates-to-others vector)."""
    if caller_is_admin:
        return True
    owner = record_owner(record)
    return bool(owner) and caller == owner


def validate_grant(role: str | None, grantee: str | None,
                   owner: str | None) -> tuple[bool, str | None]:
    """Validate an ACL grant request before it touches the DB. Returns (ok, error_code)."""
    if not grantee or not str(grantee).strip():
        return False, "missing_identity"
    if role is None:
        role = "editor"
    if role not in EDIT_ROLES:
        return False, "invalid_role"
    if owner is not None and grantee == owner:
        # The owner already has full rights; never shadow the owner with an ACL row.
        return False, "grantee_is_owner"
    return True, None

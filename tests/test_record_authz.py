"""Access-control matrix for record editing + ACL management (portal/record_authz.py).

These pin every authorization decision the editing feature can make. If any of these
flips, an unauthorized edit or an ACL escalation became possible.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "portal"))
import record_authz as az  # noqa: E402

OWNED = {"attribution": {"uploaded_by": "mahajan"}}
UNOWNED = {"attribution": {}}                 # legacy record, no uploaded_by
NO_ATTR = {}                                  # legacy record, no attribution block


# ---- can_edit_record -------------------------------------------------------

def test_owner_can_edit():
    assert az.can_edit_record(OWNED, "mahajan", False) == (True, "owner")


def test_non_owner_cannot_edit():
    ok, reason = az.can_edit_record(OWNED, "someone_else", False)
    assert ok is False and reason == "forbidden"


def test_acl_editor_can_edit():
    ok, reason = az.can_edit_record(OWNED, "labmate", False, acl_editors={"labmate"})
    assert ok is True and reason == "acl_editor"


def test_acl_editor_only_if_granted():
    ok, _ = az.can_edit_record(OWNED, "labmate", False, acl_editors={"other"})
    assert ok is False


def test_admin_can_edit_anything():
    assert az.can_edit_record(OWNED, "anybody", True)[0] is True
    assert az.can_edit_record(UNOWNED, "anybody", True)[0] is True


def test_unowned_record_is_admin_only():
    # No owner -> a normal user (even with the record) cannot edit; admin must assign first.
    assert az.can_edit_record(UNOWNED, "mahajan", False) == (False, "unowned_admin_only")
    assert az.can_edit_record(NO_ATTR, "mahajan", False) == (False, "unowned_admin_only")


def test_unauthenticated_cannot_edit():
    assert az.can_edit_record(OWNED, None, False) == (False, "unauthenticated")


def test_orcid_in_body_confers_no_rights():
    # The closed hole: a contributor/ORCID in the (client-controlled) body must NOT grant edit.
    rec = {"attribution": {"uploaded_by": "mahajan",
                           "contributors": [{"name": "Attacker", "orcid": "0000-0001"}]}}
    ok, reason = az.can_edit_record(rec, "attacker", False)   # caller is a contributor by name
    assert ok is False and reason == "forbidden"


# ---- can_manage_acl --------------------------------------------------------

def test_only_owner_or_admin_manage_acl():
    assert az.can_manage_acl(OWNED, "mahajan", False) is True      # owner
    assert az.can_manage_acl(OWNED, "anybody", True) is True       # admin
    assert az.can_manage_acl(OWNED, "labmate", False) is False     # an editor cannot re-grant
    assert az.can_manage_acl(UNOWNED, "anybody", False) is False   # unowned -> admin only


# ---- validate_grant --------------------------------------------------------

def test_grant_defaults_to_editor():
    assert az.validate_grant(None, "labmate", "mahajan") == (True, None)


def test_grant_rejects_bad_role():
    assert az.validate_grant("admin", "labmate", "mahajan") == (False, "invalid_role")
    assert az.validate_grant("owner", "labmate", "mahajan") == (False, "invalid_role")


def test_grant_rejects_empty_identity():
    assert az.validate_grant("editor", "", "mahajan")[1] == "missing_identity"
    assert az.validate_grant("editor", None, "mahajan")[1] == "missing_identity"


def test_grant_rejects_owner_as_grantee():
    # Never shadow the owner with an ACL row.
    assert az.validate_grant("editor", "mahajan", "mahajan") == (False, "grantee_is_owner")


def test_is_admin():
    assert az.is_admin(["researcher", "admin"]) is True
    assert az.is_admin(["researcher"]) is False
    assert az.is_admin([]) is False
    assert az.is_admin(None) is False

"""
tests/test_teams.py — Unit tests for the team seat management system.
"""
import pytest
import helpers.auth as auth_mod
import helpers.teams as teams_mod


@pytest.fixture(autouse=True)
def temp_users(monkeypatch, tmp_path):
    users_file = tmp_path / "users.json"
    monkeypatch.setattr(auth_mod, "_USERS_FILE", users_file)
    monkeypatch.setattr(teams_mod, "MAX_TEAM_SEATS", 3)
    yield


@pytest.fixture()
def owner():
    u = auth_mod.create_user("owner@survey.com", "passw0rd123", tier="team")
    return auth_mod.get_user(u["id"])


class TestInviteMember:
    def test_generates_token(self, owner):
        ok, msg, token = teams_mod.invite_member(owner, "new@survey.com")
        assert ok is True and token != ""

    def test_creates_stub_user_if_not_exists(self, owner):
        teams_mod.invite_member(owner, "stranger@survey.com")
        assert auth_mod.find_user_by_email("stranger@survey.com") is not None

    def test_rejects_self_invite(self, owner):
        ok, msg, _ = teams_mod.invite_member(owner, owner["email"])
        assert ok is False and "yourself" in msg.lower()

    def test_rejects_invalid_email(self, owner):
        ok, _, _ = teams_mod.invite_member(owner, "not-an-email")
        assert ok is False

    def test_rejects_when_team_full(self, owner):
        for i in range(2):
            ok, _, token = teams_mod.invite_member(owner, f"m{i}@survey.com")
            assert ok
            teams_mod.accept_invite(token)
        owner2 = auth_mod.get_user(owner["id"])
        ok, msg, _ = teams_mod.invite_member(owner2, "overflow@survey.com")
        assert ok is False and "full" in msg.lower()


class TestAcceptInvite:
    def test_happy_path(self, owner):
        _, _, token = teams_mod.invite_member(owner, "join@survey.com")
        ok, _ = teams_mod.accept_invite(token)
        assert ok is True
        u = auth_mod.find_user_by_email("join@survey.com")
        assert u["tier"] == "team" and u["team_role"] == "member"

    def test_rejects_invalid_token(self):
        ok, msg = teams_mod.accept_invite("bad-token")
        assert ok is False and ("invalid" in msg.lower() or "expired" in msg.lower())

    def test_rejects_reused_token(self, owner):
        _, _, token = teams_mod.invite_member(owner, "reuse@survey.com")
        teams_mod.accept_invite(token)
        ok, _ = teams_mod.accept_invite(token)
        assert ok is False


class TestRemoveMember:
    def test_owner_can_remove_member(self, owner):
        _, _, token = teams_mod.invite_member(owner, "rm@survey.com")
        teams_mod.accept_invite(token)
        member = auth_mod.find_user_by_email("rm@survey.com")
        owner2 = auth_mod.get_user(owner["id"])
        ok, _ = teams_mod.remove_member(owner2, member["id"])
        assert ok is True
        assert auth_mod.get_user(member["id"])["tier"] == "free"

    def test_owner_cannot_remove_self(self, owner):
        ok, _ = teams_mod.remove_member(owner, owner["id"])
        assert ok is False


class TestLeaveTeam:
    def test_member_can_leave(self, owner):
        _, _, token = teams_mod.invite_member(owner, "leaver@survey.com")
        teams_mod.accept_invite(token)
        member = auth_mod.find_user_by_email("leaver@survey.com")
        ok, _ = teams_mod.leave_team(member)
        assert ok is True
        assert auth_mod.get_user(member["id"])["tier"] == "free"

    def test_non_team_user_cannot_leave(self):
        solo = auth_mod.create_user("solo@survey.com", "longpassword1")
        ok, _ = teams_mod.leave_team(solo)
        assert ok is False

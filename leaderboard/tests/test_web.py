import re
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from leaderboard.invitations import (
    accept_invitation, issue_invitation, revoke_invitation, send_invitation,
    token_hash, valid_invitation,
)
from leaderboard.models import Invitation, Match, Player, User
from leaderboard.services import submit_match

PASSWORD = "Long-enough-spikeball-phrase-82"


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    RATELIMIT_ENABLE=False,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class WebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("admin", "admin@example.com", PASSWORD)
        cls.friend = User.objects.create_user("friend", "friend@example.com", PASSWORD)
        cls.players = [Player.objects.create(name=f"Player {i}") for i in range(4)]

    def match_data(self):
        return {
            **dict(zip(
                ("team1_player1", "team1_player2", "team2_player1", "team2_player2"),
                self.players,
            )),
            "team1_score": 21, "team2_score": 15,
            "played_at": timezone.now().replace(second=0, microsecond=0),
        }

    def match_post(self):
        data = self.match_data()
        return {
            key: value.pk if isinstance(value, Player) else (
                value.strftime("%Y-%m-%dT%H:%M") if key == "played_at" else value
            ) for key, value in data.items()
        } | {"submission_token": str(uuid.uuid4())}

    def test_public_pages_and_authenticated_submission(self):
        for route in ("leaderboard", "login", "password_reset"):
            self.assertEqual(self.client.get(reverse(route)).status_code, 200)
        self.assertEqual(self.client.get(reverse("player_detail", args=[self.players[0].pk])).status_code, 200)
        for route in ("player_new", "match_new"):
            self.assertEqual(self.client.post(reverse(route), {}).status_code, 302)
        self.assertEqual(Match.objects.count(), 0)
        self.client.force_login(self.friend)
        post = self.match_post()
        self.assertRedirects(self.client.post(reverse("match_new"), post), reverse("leaderboard"))
        self.assertRedirects(self.client.post(reverse("match_new"), post), reverse("leaderboard"))
        self.assertEqual(Match.objects.count(), 1)
        response = self.client.get(reverse("leaderboard"))
        self.assertContains(response, "1016")
        self.assertNotContains(response, self.friend.email)
        response = self.client.get(reverse("player_detail", args=[self.players[0].pk]))
        self.assertContains(response, "1016")
        self.assertContains(response, "chart")
        self.assertEqual(response.context["player"].wins, 1)
        self.assertEqual(response.context["player"].losses, 0)

    def test_invalid_results_and_player_creation(self):
        self.client.force_login(self.friend)
        for changes in (
            {"team1_player1": self.players[2].pk},
            {"team1_score": -1},
            {"team1_score": "3.5"},
            {"team1_score": 15},
        ):
            response = self.client.post(reverse("match_new"), self.match_post() | changes)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.assertEqual(Match.objects.count(), 0)
        response = self.client.post(reverse("player_new"), {"name": "New friend"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Player.objects.filter(name="New friend", rating=1000).exists())

    def test_csrf_and_logout(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.friend)
        self.assertEqual(client.post(reverse("match_new"), self.match_post()).status_code, 403)
        self.assertEqual(client.get(reverse("logout")).status_code, 405)

    def test_friend_cannot_use_admin_or_invite(self):
        self.client.force_login(self.friend)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)
        with self.assertRaises(PermissionDenied):
            issue_invitation(actor=self.friend, email="new@example.com")

    def test_admin_correction_and_derived_data_are_readonly(self):
        match = submit_match(
            actor=self.friend, data=self.match_data(), submission_token=uuid.uuid4(),
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin:leaderboard_match_change", args=[match.pk]))
        self.assertContains(response, "Correct")
        post = self.match_post() | {"reason": "Winner was reversed", "team1_score": 10}
        self.assertEqual(self.client.post(
            reverse("admin:leaderboard_match_correct", args=[match.pk]), post,
        ).status_code, 302)
        match.refresh_from_db()
        self.assertEqual(match.team1_score, 10)
        self.assertEqual(self.client.post(
            reverse("admin:leaderboard_match_change", args=[match.pk]),
            {"team1_score": 100},
        ).status_code, 403)
        self.assertEqual(self.client.post(
            reverse("admin:leaderboard_match_delete", args=[match.pk]), {"post": "yes"},
        ).status_code, 403)

    def test_invite_acceptance_and_token_rotation(self):
        invitation, token = issue_invitation(actor=self.admin, email="New@Example.com")
        self.assertEqual(invitation.token_hash, token_hash(token))
        self.assertNotEqual(invitation.token_hash, token)
        send_invitation(invitation, token)
        self.assertIn(token, mail.outbox[-1].body)
        _, replacement = issue_invitation(
            actor=self.admin, email=invitation.email, invitation_id=invitation.pk,
        )
        self.assertIsNone(valid_invitation(token))
        response = self.client.post(reverse("invitation_accept", args=[replacement]), {
            "username": "newfriend", "password1": PASSWORD, "password2": PASSWORD,
            "email": "attacker@example.com", "is_staff": True,
        })
        self.assertRedirects(response, reverse("login"))
        user = User.objects.get(username="newfriend")
        self.assertEqual(user.email, "new@example.com")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertIsNone(valid_invitation(replacement))
        self.assertEqual(self.client.get(
            reverse("invitation_accept", args=[replacement]),
        ).status_code, 400)

    def test_expired_revoked_and_duplicate_email_invitations(self):
        invitation, token = issue_invitation(actor=self.admin, email="next@example.com")
        Invitation.objects.filter(pk=invitation.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        with self.assertRaises(ValidationError):
            accept_invitation(token=token, username="late", password=PASSWORD)
        invitation, token = issue_invitation(actor=self.admin, email="other@example.com")
        revoke_invitation(actor=self.admin, invitation_id=invitation.pk)
        self.assertIsNone(valid_invitation(token))
        with self.assertRaises(ValidationError):
            issue_invitation(actor=self.admin, email="FRIEND@example.com")

    def test_email_failure_is_recoverable_and_resend_is_post_only(self):
        self.client.force_login(self.admin)
        with patch("leaderboard.admin.send_invitation", side_effect=OSError("offline")):
            response = self.client.post(
                reverse("admin:leaderboard_invitation_add"),
                {"email": "delivery@example.com"}, follow=True,
            )
        self.assertContains(response, "email delivery failed")
        invitation = Invitation.objects.get(email="delivery@example.com")
        url = reverse("admin:leaderboard_invitation_resend", args=[invitation.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(PUBLIC_BASE_URL="https://spikeball.example.com")
    def test_password_reset_uses_trusted_origin_and_is_single_use(self):
        response = self.client.post(reverse("password_reset"), {"email": self.friend.email})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        url = re.search(r"https://spikeball\.example\.com(/\S+)", mail.outbox[0].body).group(1)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        password = "A-different-long-spikeball-password-42"
        self.assertRedirects(self.client.post(response.url, {
            "new_password1": password, "new_password2": password,
        }), reverse("password_reset_complete"))
        self.friend.refresh_from_db()
        self.assertTrue(self.friend.check_password(password))
        response = self.client.get(url)
        self.assertFalse(response.context["validlink"])
        self.client.post(reverse("password_reset"), {"email": "unknown@example.com"})
        self.assertEqual(len(mail.outbox), 1)

    def test_invalid_reset_link_and_auth_templates(self):
        uid = urlsafe_base64_encode(force_bytes(self.friend.pk))
        response = self.client.get(reverse("password_reset_confirm", args=[uid, "bad-token"]))
        self.assertFalse(response.context["validlink"])
        self.client.force_login(self.friend)
        for name in ("password_change", "password_change_done", "password_reset_complete"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    @override_settings(RATELIMIT_ENABLE=True)
    def test_throttling_is_enforced_for_login_reset_and_acceptance(self):
        cache.clear()
        for route, limit, data in (
            ("login", 10, {"username": "absent", "password": "wrong"}),
            ("password_reset", 5, {"email": "absent@example.com"}),
        ):
            for _ in range(limit):
                self.assertNotEqual(self.client.post(reverse(route), data).status_code, 429)
            self.assertEqual(self.client.post(reverse(route), data).status_code, 429)
        url = reverse("invitation_accept", args=["invalid"])
        for _ in range(20):
            self.assertEqual(self.client.post(url, {}).status_code, 400)
        self.assertEqual(self.client.post(url, {}).status_code, 429)
        cache.clear()

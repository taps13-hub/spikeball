import os
import subprocess
import sys
import uuid
from unittest.mock import patch

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import SuspiciousOperation
from django.db import OperationalError, connection
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from leaderboard.email_features import EmailFeaturesDisabled
from leaderboard.invitations import accept_invitation, issue_invitation, send_invitation
from leaderboard.middleware import TrustedProxyMiddleware
from leaderboard.models import Invitation, Match, Player, RatingState, User

PASSWORD = "Long-enough-deployment-password-82"
STATIC_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(
    EMAIL_FEATURES_ENABLED=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    RATELIMIT_ENABLE=False,
    STORAGES=STATIC_STORAGE,
)
class NoEmailLaunchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_superuser("owner", "owner@example.com", PASSWORD)
        cls.players = [Player.objects.create(name=f"Player {i}") for i in range(4)]
        with override_settings(EMAIL_FEATURES_ENABLED=True):
            cls.invitation, cls.token = issue_invitation(
                actor=cls.owner, email="friend@example.com",
            )

    def test_public_ui_does_not_offer_email_features(self):
        for route in ("leaderboard", "login"):
            response = self.client.get(reverse(route))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Forgot password?")
            self.assertNotContains(response, "Ask a club administrator")
            self.assertContains(response, "Owner-managed")
        self.assertEqual(
            self.client.get(reverse("player_detail", args=[self.players[0].pk])).status_code,
            200,
        )
        for route in ("match_new", "player_new"):
            self.assertEqual(self.client.post(reverse(route), {}).status_code, 302)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)
        self.assertEqual(Match.objects.count(), 0)

    def test_reset_and_invitation_direct_urls_are_disabled(self):
        uid = urlsafe_base64_encode(force_bytes(self.owner.pk))
        reset_token = default_token_generator.make_token(self.owner)
        urls = [
            reverse("password_reset"),
            reverse("password_reset_done"),
            reverse("password_reset_confirm", args=[uid, reset_token]),
            reverse("password_reset_complete"),
            reverse("invitation_accept", args=[self.token]),
        ]
        with patch("django.core.mail.get_connection") as email_connection:
            for url in urls:
                for method in ("get", "post"):
                    with self.subTest(url=url, method=method):
                        response = getattr(self.client, method)(url, {
                            "email": self.owner.email,
                            "username": "newfriend",
                            "password1": PASSWORD,
                            "password2": PASSWORD,
                            "new_password1": PASSWORD + "new",
                            "new_password2": PASSWORD + "new",
                        })
                        self.assertContains(
                            response, "Email features are disabled", status_code=403,
                        )
                        self.assertIn("no-store", response.headers["Cache-Control"])
            email_connection.assert_not_called()
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(PASSWORD))
        self.assertEqual(User.objects.count(), 1)
        self.invitation.refresh_from_db()
        self.assertIsNone(self.invitation.accepted_at)

    def test_admin_and_service_invitation_entry_points_are_disabled(self):
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse("admin:index")), "Invitations")
        routes = [
            reverse("admin:leaderboard_invitation_changelist"),
            reverse("admin:leaderboard_invitation_add"),
            reverse("admin:leaderboard_invitation_change", args=[self.invitation.pk]),
        ]
        for url in routes:
            for method in ("get", "post"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url, {"email": "next@example.com"})
                    self.assertEqual(response.status_code, 403)
        for route in ("admin:leaderboard_invitation_resend", "admin:leaderboard_invitation_revoke"):
            self.assertEqual(
                self.client.post(reverse(route, args=[self.invitation.pk])).status_code, 403,
            )
        with self.assertRaises(EmailFeaturesDisabled):
            issue_invitation(actor=self.owner, email="next@example.com")
        with self.assertRaises(EmailFeaturesDisabled):
            send_invitation(self.invitation, self.token)
        with self.assertRaises(EmailFeaturesDisabled):
            accept_invitation(token=self.token, username="newfriend", password=PASSWORD)
        self.assertEqual(Invitation.objects.count(), 1)
        self.invitation.refresh_from_db()
        self.assertIsNone(self.invitation.revoked_at)
        self.assertEqual(mail.outbox, [])

    def test_owner_can_record_results_and_change_password_without_email(self):
        response = self.client.post(reverse("login"), {"username": "owner", "password": PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.assertEqual(self.client.post(reverse("player_new"), {"name": "New player"}).status_code, 302)
        response = self.client.post(reverse("match_new"), {
            "team1_player1": self.players[0].pk,
            "team1_player2": self.players[1].pk,
            "team2_player1": self.players[2].pk,
            "team2_player2": self.players[3].pk,
            "team1_score": 21,
            "team2_score": 15,
            "played_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            "submission_token": str(uuid.uuid4()),
        })
        self.assertEqual(response.status_code, 302)
        match = Match.objects.get()
        self.players[0].refresh_from_db()
        self.assertEqual(self.players[0].rating, 1016)
        self.assertEqual(self.client.get(
            reverse("admin:leaderboard_match_correct", args=[match.pk]),
        ).status_code, 200)
        response = self.client.post(reverse("password_change"), {
            "old_password": PASSWORD,
            "new_password1": PASSWORD + "new",
            "new_password2": PASSWORD + "new",
        })
        self.assertRedirects(response, reverse("password_change_done"))
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(PASSWORD + "new"))
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)
        self.assertEqual(mail.outbox, [])


@override_settings(
    DEBUG=False,
    SECURE_SSL_REDIRECT=True,
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    ALLOWED_HOSTS=["spikeball.example.com", "healthcheck.railway.app"],
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    EMAIL_FEATURES_ENABLED=False,
    STORAGES=STATIC_STORAGE,
)
class ProductionWebTests(TestCase):
    def test_fresh_migrations_include_lock_and_cache(self):
        self.assertTrue(RatingState.objects.filter(pk=1).exists())
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM rate_limit_cache")
            self.assertIsInstance(cursor.fetchone()[0], int)

    def test_health_allows_http_but_only_for_exact_probe_path_and_host(self):
        response = self.client.get("/healthz/", HTTP_HOST="healthcheck.railway.app")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok\n")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(
            self.client.head("/healthz/", HTTP_HOST="healthcheck.railway.app").status_code, 200,
        )
        self.assertEqual(
            self.client.post("/healthz/", HTTP_HOST="healthcheck.railway.app").status_code, 405,
        )
        for path in ("/", "/healthz/other/", "/accounts/login/"):
            response = self.client.get(path, HTTP_HOST="spikeball.example.com")
            self.assertEqual(response.status_code, 301)
            self.assertEqual(response.headers["Location"], "https://spikeball.example.com" + path)
        self.assertEqual(self.client.get("/healthz/", HTTP_HOST="untrusted.example").status_code, 400)

    def test_health_database_failure_is_sanitized(self):
        with patch("leaderboard.views.connection.cursor", side_effect=OperationalError("private credential")):
            with self.assertLogs("leaderboard.views", level="ERROR") as logs:
                response = self.client.get("/healthz/", HTTP_HOST="healthcheck.railway.app")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unavailable\n")
        self.assertNotIn("private credential", " ".join(logs.output))
        self.assertIn("database unavailable", " ".join(logs.output))

    def test_https_login_and_admin_csrf(self):
        User.objects.create_superuser("owner", "owner@example.com", PASSWORD)
        for route in ("login", "admin:login"):
            with self.subTest(route=route):
                client = Client(enforce_csrf_checks=True)
                headers = {
                    "HTTP_HOST": "spikeball.example.com",
                    "HTTP_X_FORWARDED_PROTO": "https",
                    "HTTP_ORIGIN": "https://spikeball.example.com",
                }
                response = client.get(reverse(route), **headers)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.cookies["csrftoken"]["secure"])
                response = client.post(reverse(route), {
                    "username": "owner", "password": PASSWORD,
                    "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
                }, **headers)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.cookies["sessionid"]["secure"])
                response = client.post(reverse("player_new"), {
                    "name": "From " + route,
                    "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
                }, **headers)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(client.post(reverse("player_new"), {
                    "name": "No CSRF token",
                }, **headers).status_code, 403)
        self.assertEqual(Player.objects.count(), 2)

    @override_settings(TRUST_PROXY_CLIENT_IP=True, RATELIMIT_ENABLE=True)
    def test_proxy_client_addresses_have_separate_login_limits(self):
        cache.clear()
        self.addCleanup(cache.clear)
        headers = {
            "HTTP_HOST": "spikeball.example.com",
            "HTTP_X_FORWARDED_PROTO": "https",
            "REMOTE_ADDR": "10.0.0.1",
        }
        for _ in range(10):
            response = self.client.post(
                reverse("login"), {}, HTTP_X_REAL_IP="192.0.2.1", **headers,
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post(
            reverse("login"), {}, HTTP_X_REAL_IP="192.0.2.1", **headers,
        ).status_code, 429)
        self.assertEqual(self.client.post(
            reverse("login"), {}, HTTP_X_REAL_IP="192.0.2.2", **headers,
        ).status_code, 200)


class ProxyConfigurationTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = TrustedProxyMiddleware(
            lambda request: HttpResponse(request.META["REMOTE_ADDR"]),
        )

    @override_settings(TRUST_PROXY_CLIENT_IP=False)
    def test_untrusted_headers_are_ignored_by_default(self):
        request = self.factory.get("/", HTTP_X_REAL_IP="192.0.2.1", HTTP_X_FORWARDED_FOR="192.0.2.2")
        self.assertEqual(self.middleware(request).content, b"127.0.0.1")

    @override_settings(TRUST_PROXY_CLIENT_IP=True)
    def test_explicit_trust_requires_one_valid_ip_except_internal_probe(self):
        for value in ("192.0.2.1", "2001:db8::1"):
            request = self.factory.get("/", HTTP_X_REAL_IP=value, HTTP_X_FORWARDED_FOR="192.0.2.2")
            self.assertEqual(self.middleware(request).content.decode(), value)
        for value in ("", "192.0.2.1, 192.0.2.2", "malformed"):
            with self.subTest(value=value), self.assertRaises(SuspiciousOperation):
                self.middleware(self.factory.get("/", HTTP_X_REAL_IP=value))
        with self.assertRaises(SuspiciousOperation):
            self.middleware(self.factory.get("/"))
        self.assertEqual(self.middleware(self.factory.get("/healthz/")).status_code, 200)
        with self.assertRaises(SuspiciousOperation):
            self.middleware(self.factory.get("/healthz/other/"))

    def test_production_settings_require_secret_and_https(self):
        base = {key: value for key, value in os.environ.items() if not key.startswith((
            "DJANGO_", "PUBLIC_BASE_URL", "EMAIL_FEATURES_ENABLED", "TRUST_PROXY_",
        ))}
        for values, expected in (
            ({"PUBLIC_BASE_URL": "https://spikeball.example.com"}, "DJANGO_SECRET_KEY"),
            ({"DJANGO_SECRET_KEY": "test-only", "PUBLIC_BASE_URL": "http://example.com"}, "HTTPS"),
        ):
            result = subprocess.run(
                [sys.executable, "-c", "import config.settings"],
                env=base | {"DJANGO_DEBUG": "0"} | values,
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)
        result = subprocess.run(
            [sys.executable, "-c", (
                "import config.settings as s; "
                "assert not s.DEBUG and not s.EMAIL_FEATURES_ENABLED; "
                "assert s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE; "
                "assert not s.TRUST_PROXY_CLIENT_IP; "
                "assert s.EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend'"
            )],
            env=base | {
                "DJANGO_DEBUG": "0", "DJANGO_SECRET_KEY": "test-only",
                "PUBLIC_BASE_URL": "https://spikeball.example.com", "EMAIL_FEATURES_ENABLED": "0",
            },
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

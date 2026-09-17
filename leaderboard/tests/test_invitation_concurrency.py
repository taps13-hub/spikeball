from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.test import TransactionTestCase, override_settings

from leaderboard.invitations import accept_invitation, issue_invitation
from leaderboard.models import User


@override_settings(EMAIL_FEATURES_ENABLED=True)
class InvitationConcurrencyTests(TransactionTestCase):
    def test_one_invitation_cannot_create_two_accounts(self):
        admin = User.objects.create_superuser("admin", "admin@example.com", "Admin-passphrase-42")
        invitation, token = issue_invitation(actor=admin, email="new@example.com")
        barrier = Barrier(2)

        def accept(username):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    accept_invitation(
                        token=token, username=username, password="A-long-invited-password-43",
                    )
                except ValidationError:
                    return False
                return True
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(accept, ["first", "second"]))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(User.objects.filter(email=invitation.email).count(), 1)

    def test_different_invitations_cannot_duplicate_an_email(self):
        admin = User.objects.create_superuser("admin", "admin@example.com", "Admin-passphrase-42")
        tokens = [
            issue_invitation(actor=admin, email="shared@example.com")[1] for _ in range(2)
        ]
        barrier = Barrier(2)

        def accept(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    accept_invitation(
                        token=tokens[index], username=f"user{index}",
                        password="A-long-invited-password-43",
                    )
                except ValidationError:
                    return False
                return True
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(accept, range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(User.objects.filter(email="shared@example.com").count(), 1)

from datetime import timedelta
from itertools import combinations
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from leaderboard.models import (
    Invitation, Match, MatchRevision, PLAYER_FIELDS, Player, RatingHistory, RatingState, User,
)


class ModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="recorder", email="recorder@example.com")
        cls.players = [Player.objects.create(name=f"Player {index}") for index in range(4)]

    def match_data(self):
        return {
            **dict(zip(PLAYER_FIELDS, self.players)),
            "team1_score": 21, "team2_score": 18, "created_by": self.user,
        }

    def test_default_player_and_duplicate_names(self):
        same = Player.objects.create(name=self.players[0].name)
        self.assertEqual(same.rating, 1000)
        self.assertNotEqual(str(same), str(self.players[0]))
        self.assertIn(str(same.pk), str(same))
        self.assertTrue(RatingState.objects.filter(pk=1).exists())

    def test_user_email_normalization_and_required_email(self):
        user = User(username="normalized", email="  MiXeD@EXAMPLE.COM ")
        user.set_unusable_password()
        user.full_clean()
        user.save()
        self.assertEqual(user.email, "mixed@example.com")
        user.email = "  OTHER@EXAMPLE.COM "
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.email, "other@example.com")
        with self.assertRaises(ValidationError):
            User(username="blank").full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create(username="empty", email="  ")

    def test_email_constraint_also_blocks_bypassed_normalization(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.bulk_create([User(username="duplicate", email="RECORDER@example.com")])
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.bulk_create([User(username="empty", email="")])

    def test_all_six_distinct_player_constraints(self):
        for left, right in combinations(PLAYER_FIELDS, 2):
            data = self.match_data()
            data[right] = data[left]
            with self.subTest(left=left, right=right):
                with self.assertRaises(ValidationError):
                    Match(**data).full_clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Match.objects.create(**data)

    def test_draw_and_negative_scores(self):
        for scores in ((1, 1), (-1, 1), (1, -1)):
            data = {**self.match_data(), "team1_score": scores[0], "team2_score": scores[1]}
            with self.subTest(scores=scores):
                with self.assertRaises(ValidationError):
                    Match(**data).full_clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Match.objects.create(**data)

    def test_scores_do_not_silently_truncate_or_coerce(self):
        for score in (True, 1.5, "21"):
            with self.subTest(score=score):
                match = Match(**{**self.match_data(), "team1_score": score})
                with self.assertRaises(ValidationError):
                    match.full_clean()
                with self.assertRaises(ValidationError), transaction.atomic():
                    match.save()

    def test_history_token_and_protected_references(self):
        match = Match.objects.create(**self.match_data())
        history = RatingHistory.objects.create(player=self.players[0], match=match, rating=1016)
        with self.assertRaises(IntegrityError), transaction.atomic():
            RatingHistory.objects.create(player=self.players[0], match=match, rating=1016)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Match.objects.create(**self.match_data(), submission_token=match.submission_token)
        with self.assertRaises(ProtectedError):
            self.players[0].delete()
        with self.assertRaises(ProtectedError):
            self.user.delete()
        match.delete()
        self.assertFalse(RatingHistory.objects.filter(pk=history.pk).exists())

    def test_audit_prevents_match_deletion_and_invitation_normalizes(self):
        match = Match.objects.create(**self.match_data())
        MatchRevision.objects.create(
            match=match, actor=self.user, action="correct", reason="Fix score", before={}, after={},
        )
        with self.assertRaises(ProtectedError):
            match.delete()
        invitation = Invitation.objects.create(
            email="  FRIEND@EXAMPLE.COM ", token_hash=uuid4().hex,
            expires_at=timezone.now() + timedelta(days=1), invited_by=self.user,
        )
        self.assertEqual(invitation.email, "friend@example.com")

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from leaderboard.elo import calculate_elo_update
from leaderboard.models import Match, MatchRevision, PLAYER_FIELDS, Player, RatingHistory, RatingState, User
from leaderboard.services import add_player, correct_match, submit_match


class ServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_user(username="friend", email="friend@example.com")
        cls.admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="not-used",
        )
        cls.players = [add_player(actor=cls.actor, name=f"Player {index}") for index in range(5)]
        cls.now = timezone.now()

    def data(self, **changes):
        return {
            **dict(zip(PLAYER_FIELDS, self.players)), "team1_score": 21, "team2_score": 15,
            "played_at": self.now, **changes,
        }

    def submit(self, **changes):
        return submit_match(actor=self.actor, data=self.data(**changes), submission_token=uuid4())

    def derived_state(self):
        return (
            list(Player.objects.order_by("pk").values_list("pk", "rating")),
            list(RatingHistory.objects.order_by("match_id", "player_id").values_list("match_id", "player_id", "rating")),
        )

    def assert_replayed(self):
        ratings = dict.fromkeys(Player.objects.values_list("pk", flat=True), Decimal("1000"))
        active = Match.objects.filter(voided_at__isnull=True).order_by("played_at", "pk")
        self.assertEqual(RatingHistory.objects.count(), active.count() * 4)
        for match in active:
            ids = [getattr(match, f"{field}_id") for field in PLAYER_FIELDS]
            result = calculate_elo_update([ratings[pk] for pk in ids], [match.team1_score, match.team2_score])
            for pk, rating in zip(ids, result):
                self.assertEqual(RatingHistory.objects.get(match=match, player_id=pk).rating, rating)
                ratings[pk] = rating
        self.assertEqual(dict(Player.objects.values_list("pk", "rating")), ratings)

    def test_initial_result_and_new_player(self):
        self.submit()
        self.assertEqual(
            list(Player.objects.order_by("pk").values_list("rating", flat=True)),
            [1016, 1016, 984, 984, 1000],
        )
        player = add_player(actor=self.actor, name="  New friend  ")
        self.assertEqual(player.name, "New friend")
        self.assertEqual(player.rating, 1000)
        self.assert_replayed()
        with self.assertRaises(ValidationError):
            add_player(actor=self.actor, name="  ")

    def test_backdating_and_timestamp_tie_order(self):
        later = self.submit()
        earlier = self.submit(team1_score=10, team2_score=21, played_at=self.now - timedelta(days=1))
        tied = self.submit()
        self.assertEqual(list(Match.objects.values_list("pk", flat=True)), [earlier.pk, later.pk, tied.pk])
        self.assertEqual(RatingHistory.objects.get(match=earlier, player=self.players[0]).rating, 984)
        self.assert_replayed()

    def test_corrections_scores_players_dates_and_void(self):
        first = self.submit()
        self.submit(played_at=self.now + timedelta(days=1))
        corrected_data = self.data(
            team1_player1=self.players[4], team1_score=10, team2_score=21,
            played_at=self.now + timedelta(days=2),
        )
        correct_match(actor=self.admin, match_id=first.pk, data=corrected_data, reason="  Correct the report  ")
        revision = MatchRevision.objects.get(match=first)
        self.assertEqual(revision.action, "correct")
        self.assertEqual(revision.reason, "Correct the report")
        self.assertEqual(revision.before["team1_player1"], self.players[0].pk)
        self.assertEqual(revision.after["team1_player1"], self.players[4].pk)
        self.assertEqual(revision.after["played_at"], corrected_data["played_at"].isoformat())
        self.assert_replayed()
        voided = correct_match(actor=self.admin, match_id=first.pk, data=corrected_data, reason="Duplicate report", void=True)
        self.assertEqual(voided.voided_by_id, self.admin.pk)
        self.assertIsNotNone(voided.voided_at)
        self.assertEqual(Match.objects.count(), 2)
        self.assertFalse(RatingHistory.objects.filter(match=first).exists())
        self.assertEqual(MatchRevision.objects.filter(match=first).count(), 2)
        self.players[4].refresh_from_db()
        self.assertEqual(self.players[4].rating, 1000)
        self.assert_replayed()
        with self.assertRaises(ValidationError):
            correct_match(actor=self.admin, match_id=first.pk, data=corrected_data, reason="Restore")

    def test_void_only_match_resets_all_ratings(self):
        match = self.submit()
        correct_match(actor=self.admin, match_id=match.pk, data=self.data(), reason="Not played", void=True)
        self.assertEqual(set(Player.objects.values_list("rating", flat=True)), {1000})
        self.assertFalse(RatingHistory.objects.exists())

    def test_idempotent_retry_and_token_collisions(self):
        token = uuid4()
        first = submit_match(actor=self.actor, data=self.data(), submission_token=token)
        state = self.derived_state()
        with patch("leaderboard.services.calculate_elo_update", side_effect=AssertionError("must not replay")):
            retry = submit_match(actor=self.actor, data=self.data(), submission_token=str(token))
        self.assertEqual(first.pk, retry.pk)
        self.assertEqual(state, self.derived_state())
        for actor, data in ((self.admin, self.data()), (self.actor, self.data(team1_score=22))):
            with self.subTest(actor=actor, data=data), self.assertRaises(ValidationError):
                submit_match(actor=actor, data=data, submission_token=token)
        self.submit()
        self.assertEqual(Match.objects.count(), 2)
        self.assert_replayed()

    def test_permission_checks(self):
        inactive = User.objects.create_user(username="inactive", email="inactive@example.com", is_active=False)
        for actor in (AnonymousUser(), inactive, None):
            with self.subTest(actor=actor):
                with self.assertRaises(PermissionDenied):
                    add_player(actor=actor, name="Blocked")
                with self.assertRaises(PermissionDenied):
                    submit_match(actor=actor, data=self.data(), submission_token=uuid4())
                with self.assertRaises(PermissionDenied):
                    correct_match(actor=actor, match_id=1, data=self.data(), reason="Blocked")
        match = self.submit()
        staff = User.objects.create_user(username="staff", email="staff@example.com", is_staff=True)
        permission = Permission.objects.get(content_type__app_label="leaderboard", codename="change_match")
        friend_with_permission = User.objects.create_user(username="permitted", email="permitted@example.com")
        friend_with_permission.user_permissions.add(permission)
        for actor in (self.actor, staff, friend_with_permission):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                correct_match(actor=actor, match_id=match.pk, data=self.data(), reason="Blocked")
        staff.user_permissions.add(permission)
        staff = User.objects.get(pk=staff.pk)
        correct_match(actor=staff, match_id=match.pk, data=self.data(team1_score=22), reason="Allowed")

    def test_invalid_service_inputs(self):
        invalid = [
            self.data(team1_score=True), self.data(team1_score=1.5), self.data(team1_score="21"),
            self.data(team1_score=-1), self.data(team1_score=15),
            self.data(team1_player1=self.players[1]), self.data(team1_player1=Player(name="Unsaved")),
            self.data(played_at=self.now.replace(tzinfo=None)), self.data(played_at="yesterday"),
            self.data(rating=5000), {key: value for key, value in self.data().items() if key != "played_at"},
        ]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                submit_match(actor=self.actor, data=data, submission_token=uuid4())
        with self.assertRaises(ValidationError):
            submit_match(actor=self.actor, data=self.data(), submission_token="not-a-uuid")
        self.assertFalse(Match.objects.exists())
        match = self.submit()
        with self.assertRaises(ValidationError):
            correct_match(actor=self.admin, match_id=match.pk, data=self.data(), reason=" ")

    def test_replay_failure_rolls_back_submission_and_correction(self):
        match = self.submit()
        state = self.derived_state()
        with patch("leaderboard.services.calculate_elo_update", side_effect=RuntimeError("forced")):
            with self.assertRaisesRegex(RuntimeError, "forced"):
                self.submit(played_at=self.now - timedelta(days=1))
            with self.assertRaisesRegex(RuntimeError, "forced"):
                correct_match(actor=self.admin, match_id=match.pk, data=self.data(team1_score=22), reason="Fix")
        self.assertEqual(Match.objects.count(), 1)
        match.refresh_from_db()
        self.assertEqual(match.team1_score, 21)
        self.assertEqual(state, self.derived_state())
        self.assertFalse(MatchRevision.objects.exists())

    def test_failure_after_history_replacement_rolls_everything_back(self):
        match = self.submit()
        state = self.derived_state()
        with patch("django.db.models.query.QuerySet.bulk_update", side_effect=RuntimeError("forced")):
            with self.assertRaisesRegex(RuntimeError, "forced"):
                correct_match(actor=self.admin, match_id=match.pk, data=self.data(), reason="Void", void=True)
        match.refresh_from_db()
        self.assertIsNone(match.voided_at)
        self.assertEqual(state, self.derived_state())
        self.assertFalse(MatchRevision.objects.exists())


class ConcurrentSubmissionTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql", "Concurrency tests require real PostgreSQL.")
        RatingState.objects.get_or_create(pk=1)
        self.actor = User.objects.create_user(username="writer", email="writer@example.com")
        self.players = [add_player(actor=self.actor, name=f"Player {index}") for index in range(4)]
        self.now = timezone.now()

    def run_submissions(self, tokens):
        barrier = Barrier(2)
        actor_id = self.actor.pk
        player_ids = [player.pk for player in self.players]

        def worker(token):
            close_old_connections()
            try:
                actor = User.objects.get(pk=actor_id)
                players = [Player.objects.get(pk=pk) for pk in player_ids]
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pid = cursor.fetchone()[0]
                barrier.wait(timeout=10)
                match = submit_match(
                    actor=actor, submission_token=token,
                    data={
                        **dict(zip(PLAYER_FIELDS, players)), "team1_score": 21,
                        "team2_score": 15, "played_at": self.now,
                    },
                )
                return match.pk, backend_pid
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker, token) for token in tokens]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(len({pid for _, pid in results}), 2)
        return [pk for pk, _ in results]

    def test_concurrent_distinct_tokens_preserve_both_matches(self):
        ids = self.run_submissions([uuid4(), uuid4()])
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(Match.objects.count(), 2)
        self.assertEqual(RatingHistory.objects.count(), 8)
        expected = calculate_elo_update(calculate_elo_update([1000] * 4, [21, 15]), [21, 15])
        self.assertEqual(tuple(Player.objects.order_by("pk").values_list("rating", flat=True)), expected)

    def test_concurrent_retry_saves_only_once(self):
        token = uuid4()
        ids = self.run_submissions([token, token])
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(Match.objects.count(), 1)
        self.assertEqual(RatingHistory.objects.count(), 4)
        self.assertEqual(
            list(Player.objects.order_by("pk").values_list("rating", flat=True)),
            [1016, 1016, 984, 984],
        )

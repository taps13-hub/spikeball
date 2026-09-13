import uuid
from datetime import datetime
from decimal import Decimal
from itertools import combinations

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


PLAYER_FIELDS = (
    "team1_player1", "team1_player2", "team2_player1", "team2_player2",
)

class User(AbstractUser):
    """Inherit the AbstractUser which is an abstract base class for user models. '
    Abstract base classes 
    
    """
    email = models.EmailField(unique=True)
    REQUIRED_FIELDS = ["email"]

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(Lower("email"), name="user_email_case_unique"),
            models.CheckConstraint(condition=~models.Q(email=""), name="user_email_nonempty"),
        ]

    def clean_fields(self, exclude=None):
        self.email = self.email.strip().lower()
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        self.email = self.email.strip().lower()

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

class Player(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank="short description")
    rating = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1000"))
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} (#{self.pk})"


class ScoreField(models.PositiveIntegerField):
    def to_python(self, value):
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError("Scores must be whole numbers.", code="invalid")
        return super().to_python(value)

    def get_prep_value(self, value):
        return super().get_prep_value(self.to_python(value))


class Match(models.Model):
    team1_player1 = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="team1_p1_matches")
    team1_player2 = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="team1_p2_matches")
    team2_player1 = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="team2_p1_matches")
    team2_player2 = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="team2_p2_matches")
    team1_score = ScoreField()
    team2_score = ScoreField()
    played_at = models.DateTimeField(default=timezone.now)
    recorded_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="submitted_matches")
    submission_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="voided_matches")

    class Meta:
        ordering = ["played_at", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(team1_score__gte=0), name="match_team1_score_nonnegative"),
            models.CheckConstraint(condition=models.Q(team2_score__gte=0), name="match_team2_score_nonnegative"),
            models.CheckConstraint(condition=~models.Q(team1_score=models.F("team2_score")), name="match_no_draw"),
            *[
                models.CheckConstraint(
                    condition=~models.Q(**{left: models.F(right)}),
                    name=f"match_distinct_players_{index}",
                )
                for index, (left, right) in enumerate(combinations(PLAYER_FIELDS, 2))
            ],
        ]

    def clean(self):
        super().clean()
        players = [getattr(self, f"{name}_id") for name in PLAYER_FIELDS]
        if len([pk for pk in players if pk is not None]) != len(set(pk for pk in players if pk is not None)):
            raise ValidationError("Select four different players.")
        if self.team1_score is not None and self.team1_score == self.team2_score:
            raise ValidationError("Matches cannot end in a draw.")
        if isinstance(self.played_at, datetime) and timezone.is_naive(self.played_at):
            raise ValidationError({"played_at": "Include a time zone."})

    def __str__(self):
        return f"Match #{self.pk}"


class RatingHistory(models.Model):
    player = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="rating_history")
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="rating_history")
    rating = models.DecimalField(max_digits=18, decimal_places=6)

    class Meta:
        ordering = ["match__played_at", "match_id"]
        constraints = [
            models.UniqueConstraint(fields=["player", "match"], name="history_player_match_unique"),
        ]


class RatingState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="rating_state_singleton"),
        ]


class Invitation(models.Model):
    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="invitations_sent")
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        self.email = self.email.strip().lower()

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


class MatchRevision(models.Model):
    match = models.ForeignKey(Match, on_delete=models.PROTECT, related_name="revisions")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="match_revisions")
    action = models.CharField(max_length=20)
    reason = models.TextField()
    before = models.JSONField()
    after = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

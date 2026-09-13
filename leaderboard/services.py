from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .elo import calculate_elo_update
from .models import Match, MatchRevision, PLAYER_FIELDS, Player, RatingHistory, RatingState


MATCH_FIELDS = (*PLAYER_FIELDS, "team1_score", "team2_score", "played_at")


def _require_actor(actor):
    if not actor or not actor.is_authenticated or not actor.is_active or not actor.pk:
        raise PermissionDenied


def _validate_data(data):
    if not isinstance(data, Mapping) or set(data) != set(MATCH_FIELDS):
        raise ValidationError("Provide exactly the players, scores, and played-at time.")
    for field in PLAYER_FIELDS:
        if not isinstance(data[field], Player) or data[field].pk is None:
            raise ValidationError({field: "Select a saved player."})
    for field in ("team1_score", "team2_score"):
        score = data[field]
        if not isinstance(score, int) or isinstance(score, bool) or score < 0:
            raise ValidationError({field: "Enter a non-negative whole number."})
    if not isinstance(data["played_at"], datetime) or timezone.is_naive(data["played_at"]):
        raise ValidationError({"played_at": "Include a time zone."})


def _snapshot(match):
    return {
        **{field: getattr(match, f"{field}_id") for field in PLAYER_FIELDS},
        "team1_score": match.team1_score,
        "team2_score": match.team2_score,
        "played_at": match.played_at.isoformat(),
        "voided_at": match.voided_at.isoformat() if match.voided_at else None,
        "voided_by": match.voided_by_id,
    }


def _replay_locked():
    # ponytail: global lock + full replay suits friends; checkpoint replay if volume grows.
    players = list(Player.objects.all())
    ratings = {player.pk: Decimal("1000") for player in players}
    history = []
    for match in Match.objects.filter(voided_at__isnull=True).order_by("played_at", "id"):
        ids = [getattr(match, f"{field}_id") for field in PLAYER_FIELDS]
        updated = calculate_elo_update(
            [ratings[pk] for pk in ids], (match.team1_score, match.team2_score),
        )
        for pk, rating in zip(ids, updated):
            ratings[pk] = rating
            history.append(RatingHistory(player_id=pk, match_id=match.pk, rating=rating))
    RatingHistory.objects.all().delete()
    RatingHistory.objects.bulk_create(history, batch_size=1000)
    for player in players:
        player.rating = ratings[player.pk]
    Player.objects.bulk_update(players, ["rating"], batch_size=1000)


def add_player(*, actor, name):
    _require_actor(actor)
    if not isinstance(name, str):
        raise ValidationError({"name": "Enter a name."})
    with transaction.atomic():
        RatingState.objects.select_for_update().get(pk=1)
        player = Player(name=name.strip())
        player.full_clean()
        player.save()
        return player


def submit_match(*, actor, data, submission_token):
    _require_actor(actor)
    _validate_data(data)
    try:
        token = UUID(str(submission_token))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValidationError("Invalid submission token.") from error
    with transaction.atomic():
        RatingState.objects.select_for_update().get(pk=1)
        existing = Match.objects.filter(submission_token=token).first()
        if existing:
            matches = (
                existing.created_by_id == actor.pk
                and all(getattr(existing, f"{field}_id") == data[field].pk for field in PLAYER_FIELDS)
                and all(getattr(existing, field) == data[field] for field in MATCH_FIELDS if field not in PLAYER_FIELDS)
            )
            if not matches:
                raise ValidationError("This submission token was already used for another result.")
            return existing
        match = Match(**data, created_by=actor, submission_token=token)
        match.full_clean()
        match.save()
        _replay_locked()
        return match


def correct_match(*, actor, match_id, data, reason, void=False):
    _require_actor(actor)
    if not (actor.is_superuser or (actor.is_staff and actor.has_perm("leaderboard.change_match"))):
        raise PermissionDenied
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError({"reason": "Explain why this result is changing."})
    _validate_data(data)
    with transaction.atomic():
        RatingState.objects.select_for_update().get(pk=1)
        try:
            match = Match.objects.get(pk=match_id)
        except Match.DoesNotExist as error:
            raise ValidationError("That match no longer exists.") from error
        if match.voided_at is not None:
            raise ValidationError("Voided matches cannot be edited or restored.")
        before = _snapshot(match)
        for field in MATCH_FIELDS:
            setattr(match, field, data[field])
        if void:
            match.voided_at = timezone.now()
            match.voided_by = actor
        match.full_clean()
        match.save()
        MatchRevision.objects.create(
            match=match, actor=actor, action="void" if void else "correct",
            reason=reason.strip(), before=before, after=_snapshot(match),
        )
        _replay_locked()
        return match

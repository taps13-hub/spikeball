import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from .models import Invitation, User


def require_inviter(actor):
    if not actor.is_active or not actor.is_staff or not (
        actor.is_superuser or actor.has_perm("leaderboard.add_invitation")
    ):
        raise PermissionDenied


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def valid_invitation(token):
    return Invitation.objects.filter(
        token_hash=token_hash(token),
        expires_at__gt=timezone.now(),
        accepted_at__isnull=True,
        revoked_at__isnull=True,
    ).first()


@transaction.atomic
def issue_invitation(*, actor, email, invitation_id=None):
    require_inviter(actor)
    email = email.strip().lower()
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError("An account already exists for that email.")
    if invitation_id is None:
        invitation = Invitation(email=email, invited_by=actor)
    else:
        invitation = Invitation.objects.select_for_update().get(pk=invitation_id)
        if invitation.accepted_at or invitation.revoked_at:
            raise ValidationError("Accepted or revoked invitations cannot be resent.")
        if invitation.email != email:
            raise ValidationError("An invitation's email cannot be changed on resend.")
    token = secrets.token_urlsafe(32)
    invitation.token_hash = token_hash(token)
    invitation.expires_at = timezone.now() + timedelta(days=settings.INVITATION_DAYS)
    invitation.full_clean()
    invitation.save()
    return invitation, token


def send_invitation(invitation, token):
    url = settings.PUBLIC_BASE_URL + reverse("invitation_accept", args=[token])
    sent = send_mail(
        "You're invited to Spikeball",
        f"Create your Spikeball account:\n\n{url}\n\n"
        f"This single-use link expires at {invitation.expires_at:%Y-%m-%d %H:%M %Z}.\n"
        "If you weren't expecting this invitation, ignore this email.",
        settings.DEFAULT_FROM_EMAIL,
        [invitation.email],
    )
    if sent != 1:
        raise OSError("The email backend did not accept the invitation.")


@transaction.atomic
def revoke_invitation(*, actor, invitation_id):
    require_inviter(actor)
    invitation = Invitation.objects.select_for_update().get(pk=invitation_id)
    if invitation.accepted_at:
        raise ValidationError("An accepted invitation cannot be revoked. Disable the account instead.")
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["revoked_at"])


def accept_invitation(*, token, username, password):
    try:
        with transaction.atomic():
            invitation = Invitation.objects.select_for_update().filter(
                token_hash=token_hash(token),
            ).first()
            if not invitation or invitation.accepted_at or invitation.revoked_at or (
                invitation.expires_at <= timezone.now()
            ):
                raise ValidationError("This invitation is expired, invalid, or already used.")
            user = User(username=username, email=invitation.email)
            validate_password(password, user)
            user.set_password(password)
            user.full_clean()
            user.save()
            invitation.accepted_at = timezone.now()
            invitation.save(update_fields=["accepted_at"])
            return user
    except IntegrityError as error:
        if getattr(error.__cause__, "sqlstate", None) == "23505":
            raise ValidationError("That username or email already has an account.") from error
        raise


def password_reset_options():
    origin = urlsplit(settings.PUBLIC_BASE_URL)
    return {"domain_override": origin.netloc, "use_https": origin.scheme == "https"}

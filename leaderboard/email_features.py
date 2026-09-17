from functools import wraps

from django.conf import settings
from django.core.exceptions import PermissionDenied


class EmailFeaturesDisabled(PermissionDenied):
    pass


def require_email_features():
    if not settings.EMAIL_FEATURES_ENABLED:
        raise EmailFeaturesDisabled("Email invitations and password resets are disabled.")


def email_features_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        require_email_features()
        return view(*args, **kwargs)
    return wrapped


def context(request):
    return {"email_features_enabled": settings.EMAIL_FEATURES_ENABLED}

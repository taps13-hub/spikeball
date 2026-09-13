import smtplib

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .forms import CorrectionForm, InvitationForm, MATCH_DATA_FIELDS
from .invitations import (
    issue_invitation, require_inviter, revoke_invitation, send_invitation,
)
from .models import Invitation, Match, MatchRevision, Player, RatingHistory, RatingState, User
from .services import add_player, correct_match

admin.site.site_header = "Spikeball administration"
admin.site.site_title = "Spikeball admin"
admin.site.login = ratelimit(
    key="ip", rate="10/m", method="POST", block=True,
)(admin.site.login)


class AccountCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")


class AccountChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class AccountAdmin(UserAdmin):
    add_form = AccountCreationForm
    form = AccountChangeForm
    add_fieldsets = ((None, {"fields": ("username", "email", "password1", "password2")}),)

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False


class NoDeleteAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Player)
class PlayerAdmin(NoDeleteAdmin):
    list_display = ("name", "rating", "created_at")
    readonly_fields = ("rating", "created_at")
    search_fields = ("name",)

    def save_model(self, request, obj, form, change):
        if change:
            with transaction.atomic():
                RatingState.objects.select_for_update().get(pk=1)
                obj.save(update_fields=["name"])
        else:
            saved = add_player(actor=request.user, name=obj.name)
            obj.pk, obj.rating, obj.created_at = saved.pk, saved.rating, saved.created_at
            obj._state = saved._state


class ReadOnlyAdmin(NoDeleteAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)


@admin.register(Match)
class MatchAdmin(ReadOnlyAdmin):
    list_display = ("id", "played_at", "team1_score", "team2_score", "created_by", "voided_at")
    list_filter = ("voided_at",)
    change_form_template = "admin/leaderboard/match/change_form.html"

    def get_urls(self):
        return [path(
            "<int:object_id>/correct/", self.admin_site.admin_view(self.correct_view),
            name="leaderboard_match_correct",
        )] + super().get_urls()

    def correct_view(self, request, object_id):
        if not request.user.has_perm("leaderboard.change_match"):
            raise PermissionDenied
        match = get_object_or_404(Match, pk=object_id, voided_at__isnull=True)
        form = CorrectionForm(request.POST or None, instance=match)
        if request.method == "POST" and form.is_valid():
            try:
                correct_match(
                    actor=request.user, match_id=object_id,
                    data={key: form.cleaned_data[key] for key in MATCH_DATA_FIELDS},
                    reason=form.cleaned_data["reason"], void=form.cleaned_data["void"],
                )
            except ValidationError as error:
                form.add_error(None, "; ".join(error.messages))
            else:
                messages.success(request, "Result updated; ratings rebuilt.")
                return redirect("admin:leaderboard_match_changelist")
        return render(request, "admin/leaderboard/form.html", {
            **self.admin_site.each_context(request),
            "form": form, "title": f"Correct match #{object_id}", "opts": self.model._meta,
        })

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return super().change_view(request, object_id, form_url, {
            **(extra_context or {}),
            "can_correct": request.user.has_perm("leaderboard.change_match"),
            "correction_url": reverse("admin:leaderboard_match_correct", args=[object_id]),
        })


@admin.register(RatingHistory)
class RatingHistoryAdmin(ReadOnlyAdmin):
    list_display = ("player", "match", "rating")
    list_select_related = ("player", "match")


@admin.register(MatchRevision)
class MatchRevisionAdmin(ReadOnlyAdmin):
    list_display = ("match", "action", "actor", "created_at", "reason")
    list_select_related = ("match", "actor")


@admin.register(Invitation)
class InvitationAdmin(ReadOnlyAdmin):
    list_display = ("email", "expires_at", "accepted_at", "revoked_at")
    exclude = ("token_hash",)
    change_form_template = "admin/leaderboard/invitation/change_form.html"

    def get_readonly_fields(self, request, obj=None):
        return tuple(name for name in super().get_readonly_fields(request, obj)
                     if name != "token_hash")

    def has_add_permission(self, request):
        return request.user.has_perm("leaderboard.add_invitation")

    def get_urls(self):
        return [
            path("<int:object_id>/resend/", self.admin_site.admin_view(self.resend_view),
                 name="leaderboard_invitation_resend"),
            path("<int:object_id>/revoke/", self.admin_site.admin_view(self.revoke_view),
                 name="leaderboard_invitation_revoke"),
        ] + super().get_urls()

    def deliver(self, request, invitation, token):
        try:
            send_invitation(invitation, token)
        except (smtplib.SMTPException, OSError):
            messages.error(
                request,
                "Invitation saved, but email delivery failed. Check email configuration "
                "and use Resend on this invitation.",
            )
        else:
            messages.success(request, "Invitation email sent.")

    def add_view(self, request, form_url="", extra_context=None):
        require_inviter(request.user)
        form = InvitationForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                invitation, token = issue_invitation(
                    actor=request.user, email=form.cleaned_data["email"],
                )
            except ValidationError as error:
                form.add_error(None, "; ".join(error.messages))
            else:
                self.deliver(request, invitation, token)
                return redirect("admin:leaderboard_invitation_change", invitation.pk)
        return render(request, "admin/leaderboard/form.html", {
            **self.admin_site.each_context(request),
            "form": form, "title": "Invite a friend", "opts": self.model._meta,
        })

    @method_decorator(require_POST)
    def resend_view(self, request, object_id):
        require_inviter(request.user)
        invitation = get_object_or_404(Invitation, pk=object_id)
        try:
            invitation, token = issue_invitation(
                actor=request.user, email=invitation.email, invitation_id=object_id,
            )
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
        else:
            self.deliver(request, invitation, token)
        return redirect("admin:leaderboard_invitation_change", object_id)

    @method_decorator(require_POST)
    def revoke_view(self, request, object_id):
        require_inviter(request.user)
        get_object_or_404(Invitation, pk=object_id)
        try:
            revoke_invitation(actor=request.user, invitation_id=object_id)
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
        else:
            messages.success(request, "Invitation revoked.")
        return redirect("admin:leaderboard_invitation_change", object_id)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return super().change_view(request, object_id, form_url, {
            **(extra_context or {}),
            "can_invite": self.has_add_permission(request),
            "resend_url": reverse("admin:leaderboard_invitation_resend", args=[object_id]),
            "revoke_url": reverse("admin:leaderboard_invitation_revoke", args=[object_id]),
        })

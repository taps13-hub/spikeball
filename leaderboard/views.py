import uuid

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited

from .forms import InvitationAcceptForm, MATCH_DATA_FIELDS, MatchForm, PlayerForm
from .invitations import accept_invitation, password_reset_options, valid_invitation
from .models import Match, PLAYER_FIELDS, Player, RatingHistory, User
from .services import add_player, submit_match

def records():
    totals = {}
    for match in Match.objects.filter(voided_at__isnull=True).values(
        *[f"{field}_id" for field in PLAYER_FIELDS], "team1_score", "team2_score",
    ):
        for index, field in enumerate(PLAYER_FIELDS):
            won = (index < 2) == (match["team1_score"] > match["team2_score"])
            counts = totals.setdefault(match[f"{field}_id"], [0, 0])
            counts[0 if won else 1] += 1
    return totals


def leaderboard(request):
    totals = records()
    players = list(Player.objects.order_by("-rating", "name", "id"))
    for player in players:
        player.wins, player.losses = totals.get(player.pk, (0, 0))
    return render(request, "leaderboard/index.html", {"players": players})


def player_detail(request, pk):
    player = get_object_or_404(Player, pk=pk)
    player.wins, player.losses = records().get(pk, (0, 0))
    history = list(RatingHistory.objects.filter(player=player).select_related("match")
                   .order_by("match__played_at", "match_id"))
    match_filter = Q()
    for field in PLAYER_FIELDS:
        match_filter |= Q(**{field: player})
    matches = Match.objects.filter(match_filter, voided_at__isnull=True).select_related(
        *PLAYER_FIELDS,
    ).order_by("-played_at", "-id")[:20]
    chart_data = {
        "labels": [item.match.played_at.isoformat() for item in history],
        "ratings": [float(item.rating) for item in history],
    }
    return render(request, "leaderboard/player_detail.html", {
        "player": player, "history": history, "matches": matches, "chart_data": chart_data,
    })


@login_required
@require_http_methods(["GET", "POST"])
def player_new(request):
    form = PlayerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            player = add_player(actor=request.user, name=form.cleaned_data["name"])
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
        else:
            messages.success(request, "Player added.")
            return redirect("player_detail", pk=player.pk)
    return render(request, "leaderboard/form.html", {
        "form": form, "title": "Add a player", "button": "Add player",
    })


@login_required
@require_http_methods(["GET", "POST"])
def match_new(request):
    form = MatchForm(request.POST or None, initial={"submission_token": uuid.uuid4()})
    if request.method == "POST" and form.is_valid():
        try:
            submit_match(
                actor=request.user,
                data={key: form.cleaned_data[key] for key in MATCH_DATA_FIELDS},
                submission_token=form.cleaned_data["submission_token"],
            )
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
        else:
            messages.success(request, "Result recorded and ratings updated.")
            return redirect("leaderboard")
    return render(request, "leaderboard/form.html", {
        "form": form, "title": "Record a match", "button": "Save result",
    })


@never_cache
@sensitive_post_parameters("password1", "password2")
@ratelimit(key="ip", rate="20/h", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def invitation_accept(request, token):
    invitation = valid_invitation(token)
    if not invitation:
        return render(request, "leaderboard/invitation_invalid.html", status=400)
    form = InvitationAcceptForm(
        request.POST or None, instance=User(email=invitation.email),
    )
    if request.method == "POST" and form.is_valid():
        try:
            accept_invitation(
                token=token, username=form.cleaned_data["username"],
                password=form.cleaned_data["password1"],
            )
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
        else:
            messages.success(request, "Account created. You can now log in.")
            return redirect("login")
    return render(request, "leaderboard/form.html", {
        "form": form, "title": "Accept your invitation", "button": "Create account",
    })


@method_decorator(ratelimit(key="ip", rate="10/m", method="POST", block=True), name="dispatch")
class LoginView(auth_views.LoginView):
    pass


@method_decorator(ratelimit(key="ip", rate="5/h", method="POST", block=True), name="dispatch")
class PasswordResetView(auth_views.PasswordResetView):
    success_url = reverse_lazy("password_reset_done")

    def form_valid(self, form):
        form.save(
            **password_reset_options(),
            token_generator=self.token_generator,
            from_email=self.from_email,
            email_template_name=self.email_template_name,
            subject_template_name=self.subject_template_name,
            request=self.request,
            html_email_template_name=self.html_email_template_name,
            extra_email_context=self.extra_email_context,
        )
        return redirect(self.get_success_url())


def permission_denied(request, exception):
    if isinstance(exception, Ratelimited):
        response = render(request, "leaderboard/error.html", {
            "title": "Too many attempts",
            "detail": "Please wait before trying again.",
        }, status=429)
        response["Retry-After"] = "3600"
        return response
    return render(request, "leaderboard/error.html", {
        "title": "Permission denied", "detail": "Your account cannot perform this action.",
    }, status=403)

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from .models import Match, Player, User


class PlayerForm(forms.ModelForm):
    class Meta:
        model = Player
        fields = ["name", "description"]


class MatchForm(forms.ModelForm):
    submission_token = forms.UUIDField(widget=forms.HiddenInput)

    class Meta:
        model = Match
        fields = [
            "team1_player1",
            "team1_player2",
            "team2_player1",
            "team2_player2",
            "team1_score",
            "team2_score",
            "played_at",
        ]
        widgets = {
            "played_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["played_at"].help_text = (
            f"Time zone: {timezone.get_current_timezone_name()}. "
            "Earlier results recalculate later ratings."
        )
        for field in (
            "team1_player1",
            "team1_player2",
            "team2_player1",
            "team2_player2",
        ):
            self.fields[field].queryset = Player.objects.order_by("name", "id")


class CorrectionForm(MatchForm):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    void = forms.BooleanField(
        required=False,
        label="Void this result",
        help_text="Retain the result for audit, but exclude it from rankings.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        del self.fields["submission_token"]


class InvitationForm(forms.Form):
    email = forms.EmailField()

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account already exists for that email.")
        return email


class InvitationAcceptForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ["username"]


MATCH_DATA_FIELDS = tuple(MatchForm.Meta.fields)

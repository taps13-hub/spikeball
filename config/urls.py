from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from leaderboard import views
from leaderboard.email_features import email_features_required

urlpatterns = [
    path("healthz/", views.health, name="health"),
    path("", views.leaderboard, name="leaderboard"),
    path("players/new/", views.player_new, name="player_new"),
    path("players/<int:pk>/", views.player_detail, name="player_detail"),
    path("matches/new/", views.match_new, name="match_new"),
    path("invitations/accept/<str:token>/", views.invitation_accept, name="invitation_accept"),
    path("accounts/login/", views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "accounts/password_change/",
        auth_views.PasswordChangeView.as_view(),
        name="password_change",
    ),
    path(
        "accounts/password_change/done/",
        auth_views.PasswordChangeDoneView.as_view(),
        name="password_change_done",
    ),
    path(
        "accounts/password_reset/",
        email_features_required(views.PasswordResetView.as_view()),
        name="password_reset",
    ),
    path(
        "accounts/password_reset/done/",
        email_features_required(auth_views.PasswordResetDoneView.as_view()),
        name="password_reset_done",
    ),
    path(
        "accounts/reset/<uidb64>/<token>/",
        email_features_required(auth_views.PasswordResetConfirmView.as_view()),
        name="password_reset_confirm",
    ),
    path(
        "accounts/reset/done/",
        email_features_required(auth_views.PasswordResetCompleteView.as_view()),
        name="password_reset_complete",
    ),
    path("admin/", admin.site.urls),
]
handler403 = views.permission_denied

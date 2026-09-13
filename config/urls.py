from django.contrib import admin
from django.urls import include, path

from leaderboard import views

urlpatterns = [
    path("", views.leaderboard, name="leaderboard"),
    path("players/new/", views.player_new, name="player_new"),
    path("players/<int:pk>/", views.player_detail, name="player_detail"),
    path("matches/new/", views.match_new, name="match_new"),
    path("invitations/accept/<str:token>/", views.invitation_accept, name="invitation_accept"),
    path("accounts/login/", views.LoginView.as_view(), name="login"),
    path("accounts/password_reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("admin/", admin.site.urls),
]
handler403 = views.permission_denied

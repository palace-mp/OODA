from django.contrib.auth import views as auth
from django.urls import include, path

from accounts import views as accounts
from accounts.forms import LoginForm
from board import views as board

urlpatterns = [
    path("", include("board.urls")),
    path("login/", auth.LoginView.as_view(authentication_form=LoginForm, redirect_authenticated_user=True),
         name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("start/", accounts.start, name="start"),
    path("signup/", accounts.signup, name="signup"),
    path("join/<str:token>/", accounts.join, name="join"),
    path("no-account/", accounts.no_workspace, name="no_workspace"),
    path("me/", accounts.profile, name="profile"),
    path("me/password/", auth.PasswordChangeView.as_view(success_url="/me/"), name="password_change"),
    path("password/reset/", auth.PasswordResetView.as_view(), name="password_reset"),
    path("password/reset/sent/", auth.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", auth.PasswordResetConfirmView.as_view(),
         name="password_reset_confirm"),
    path("password/reset/done/", auth.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("settings/", accounts.workspace_settings, name="settings"),
    path("settings/invite/", accounts.create_invite, name="create_invite"),
    path("settings/invite/<int:pk>/revoke/", accounts.revoke_invite, name="revoke_invite"),
    path("settings/member/<int:pk>/", accounts.change_member, name="change_member"),
    path("settings/publish/", accounts.publish, name="publish"),
    path("settings/field/", accounts.add_field, name="add_field"),
    path("settings/field/<int:pk>/", accounts.change_field, name="change_field"),
    path("settings/option/<int:pk>/", accounts.change_option, name="change_option"),
    path("p/<str:token>/", board.public_index, name="public_index"),
    path("p/<str:token>/<int:number>/", board.public_item, name="public_item"),
]

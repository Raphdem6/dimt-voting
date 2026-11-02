from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("login-help/", views.login_help, name="login_help"),
    path("check/", views.check_registration, name="check"),
    path("results/", views.results, name="results"),
    path("results/<int:election_id>/", views.results, name="results_for"),
    path("candidates/", views.candidates, name="candidates"),
    path("candidates/<int:election_id>/", views.candidates, name="candidates_for"),
    path("api/public/results/<int:election_id>/", views.results_api, name="results_api"),
    path("vote/<int:election_id>/", views.ballot, name="ballot"),
    path("vote/<int:election_id>/receipt/", views.receipt, name="receipt"),
    path("theme.css", views.theme_css, name="theme_css"),
    path("audit/verify/", views.audit_check, name="audit_check"),
]

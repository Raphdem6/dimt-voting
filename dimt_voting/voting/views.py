import re
from datetime import timedelta
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET, require_POST

from . import security, services
from .models import (Candidate, Election, LoginIssue, SiteSettings, Student, VoterRecord,
                     mask_name, normalize_id)


# ------------------------------------------------------------------ student session helpers
def get_student(request):
    pk = request.session.get("student_pk")
    if not pk:
        return None
    student = Student.objects.filter(pk=pk).first()
    if not student or student.is_blacklisted or not student.is_eligible:
        request.session.pop("student_pk", None)
        return None
    return student


def student_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        student = get_student(request)
        if not student:
            return redirect(f"{reverse('login')}?{urlencode({'next': request.get_full_path()})}")
        request.student = student
        return view(request, *args, **kwargs)
    return wrapper


# ------------------------------------------------------------------ public pages
def home(request):
    elections = Election.objects.filter(is_active=True)
    student = get_student(request)
    voted_ids = set(VoterRecord.objects.filter(student=student).values_list("election_id", flat=True)) if student else set()
    return render(request, "voting/home.html", {"elections": elections, "voted_ids": voted_ids})


def _pick_election(election_id):
    elections = Election.objects.filter(is_active=True)
    election = get_object_or_404(elections, pk=election_id) if election_id else elections.first()
    return election, elections


def results(request, election_id=None):
    """PUBLIC dashboard - no login required."""
    election, elections = _pick_election(election_id)
    return render(request, "voting/results.html", {"election": election, "elections": elections})


@cache_page(5)   # short cache keeps heavy public traffic off the database
def results_api(request, election_id):
    election = get_object_or_404(Election, pk=election_id, is_active=True)
    return JsonResponse(services.public_results(election))


def candidates(request, election_id=None):
    """PUBLIC: every candidate with their programmes, so students can read before voting."""
    election, elections = _pick_election(election_id)
    positions = []
    if election:
        positions = election.positions.prefetch_related("candidates__programs")
        for p in positions:
            p.shown = [c for c in p.candidates.all() if c.is_approved]
    return render(request, "voting/candidates.html", {"election": election, "elections": elections, "positions": positions})


def theme_css(request):
    """Website background chosen by the admin, delivered as a normal stylesheet (keeps the strict CSP)."""
    s = SiteSettings.load()
    color = s.background_color if re.match(r"^#[0-9a-fA-F]{6}$", s.background_color) else "#f6f8f4"
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    css = f"body{{background-color:{color}}}\n"
    if s.background_image:
        a = round(s.fade_percent / 100, 2)
        url = s.background_image.url.replace('"', "").replace("\\", "")
        css += (f"body::before{{content:'';position:fixed;inset:0;z-index:-1;"
                f"background:linear-gradient(rgba({r},{g},{b},{a}),rgba({r},{g},{b},{a})),url(\"{url}\") center/cover no-repeat}}\n")
    resp = HttpResponse(css, content_type="text/css")
    resp["Cache-Control"] = "public, max-age=60"
    return resp


# ------------------------------------------------------------------ student login (name + ID number)
def login_view(request):
    if get_student(request):
        return redirect("home")
    ip = request.client_ip
    ctx = {"failed_id": ""}
    if request.method == "POST":
        ip_key = f"loginfail:{ip}"
        sid = normalize_id(request.POST.get("student_id", ""))[:40]
        name = request.POST.get("full_name", "")[:150]

        if security.count(ip_key) >= settings.LOGIN_IP_LIMIT:
            messages.error(request, "Too many failed attempts from this device. Please wait 15 minutes, or use the login problem page.")
            ctx["failed_id"] = sid
            return render(request, "voting/login.html", ctx, status=429)

        student = Student.objects.filter(student_id=sid).first() if sid else None

        if student and student.is_locked:
            messages.error(request, "This account is temporarily locked after too many wrong attempts. Try again later or report the problem.")
            ctx["failed_id"] = sid
            return render(request, "voting/login.html", ctx)

        if student and student.name_matches(name):
            if student.is_blacklisted:
                services.log("LOGIN_REFUSED_BLACKLISTED", student.student_id)
                messages.error(request, "Your access has been suspended. If you think this is a mistake, send your ID on the login problem page.")
                ctx["failed_id"] = sid
                return render(request, "voting/login.html", ctx)
            if not student.is_eligible:
                messages.error(request, "Your ID is on the list but is not marked eligible to vote. Please report it.")
                ctx["failed_id"] = sid
                return render(request, "voting/login.html", ctx)
            # success
            student.failed_logins, student.locked_until = 0, None
            student.save(update_fields=["failed_logins", "locked_until"])
            request.session.cycle_key()                    # prevents session fixation
            request.session["student_pk"] = student.pk
            security.reset(ip_key)
            services.log("LOGIN", student.student_id)
            nxt = request.GET.get("next", "")
            if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                return redirect(nxt)
            return redirect("home")

        # failure (wrong ID or wrong name) - same message either way so nothing leaks
        security.hit(ip_key)
        if student:
            student.failed_logins += 1
            if student.failed_logins >= settings.MAX_FAILED_LOGINS:
                student.locked_until = timezone.now() + timedelta(minutes=settings.LOCKOUT_MINUTES)
                student.failed_logins = 0
                services.log("ACCOUNT_LOCKED", student.student_id)
            student.save(update_fields=["failed_logins", "locked_until"])
        messages.error(request, "We could not match that name and ID number to the voter list.")
        ctx["failed_id"] = sid
        return render(request, "voting/login.html", ctx)
    return render(request, "voting/login.html", ctx)


@require_POST
def logout_view(request):
    request.session.pop("student_pk", None)
    request.session.cycle_key()
    return redirect("home")


# ------------------------------------------------------------------ "error handler" page (login problems)
def login_help(request):
    """Students who cannot log in send their ID number here; the admin sees it in the control panel."""
    ip = request.client_ip
    ctx = {"problems": LoginIssue.PROBLEMS,
           "prefill_id": normalize_id(request.GET.get("student_id", ""))[:40]}
    if request.method == "POST":
        if request.POST.get("website"):                       # hidden trap field: bots fill it in
            return render(request, "voting/login_help_done.html", {"reference": "HELP-00000"})
        key = f"helpsent:{ip}"
        if security.count(key) >= 5:
            messages.error(request, "You have sent several reports already. Please wait a while for the election officers to respond.")
            return render(request, "voting/login_help.html", ctx, status=429)
        sid = normalize_id(request.POST.get("student_id", ""))
        name = " ".join(request.POST.get("full_name", "").split())[:150]
        problem = request.POST.get("problem", "other")
        if problem not in dict(LoginIssue.PROBLEMS):
            problem = "other"
        if len(sid) < 2 or len(sid) > 40:
            messages.error(request, "Please type your ID number.")
            ctx["prefill_id"] = sid
            return render(request, "voting/login_help.html", ctx)
        issue = LoginIssue.objects.create(
            student_id_entered=sid, name_entered=name, problem=problem, ip=ip,
            message=request.POST.get("message", "")[:600], phone=request.POST.get("phone", "")[:30])
        security.hit(key, 3600)
        services.log("LOGIN_ISSUE", ip, f"{issue.reference} id={sid}")
        return render(request, "voting/login_help_done.html", {"reference": issue.reference})
    return render(request, "voting/login_help.html", ctx)


def check_registration(request):
    """Student types their ID and sees whether it is on the voter list (name shown masked)."""
    ctx = {"checked": False}
    if request.method == "POST":
        key = f"checkreg:{request.client_ip}"
        if security.count(key) >= 15:
            messages.error(request, "Too many checks. Please wait a few minutes.")
            return render(request, "voting/check.html", ctx, status=429)
        security.hit(key)
        sid = normalize_id(request.POST.get("student_id", ""))[:40]
        student = Student.objects.filter(student_id=sid).first() if sid else None
        ctx.update(checked=True, sid=sid, found=bool(student),
                   masked=mask_name(student.full_name) if student else "",
                   eligible=bool(student and student.is_eligible))
    return render(request, "voting/check.html", ctx)


# ------------------------------------------------------------------ voting
def _parse_choices(request, election):
    choices = {}
    for pos in election.positions.all():
        choices[pos.id] = [int(x) for x in request.POST.getlist(f"pos_{pos.id}") if x.isdigit()]
    return choices


@student_required
def ballot(request, election_id):
    election = get_object_or_404(Election, pk=election_id, is_active=True)
    student = request.student
    if VoterRecord.objects.filter(election=election, student=student).exists():
        messages.info(request, "You have already voted in this election.")
        return redirect("home")
    if not election.is_open:
        messages.error(request, "This election is not open for voting right now.")
        return redirect("home")

    positions = election.positions.prefetch_related("candidates__programs")
    for p in positions:
        p.ballot_candidates = [c for c in p.candidates.all() if c.is_approved]

    if request.method == "POST":
        choices = _parse_choices(request, election)
        if request.POST.get("step") == "confirm":
            try:
                record = services.cast_ballot(student, election, choices)
            except services.VoteError as e:
                messages.error(request, str(e))
                return redirect("ballot", election_id=election.id)
            return redirect("receipt", election_id=election.id)

        summary = []
        for p in positions:
            picked = [c for c in p.ballot_candidates if c.id in choices.get(p.id, [])]
            if len(picked) > p.max_choices:
                messages.error(request, f"Choose at most {p.max_choices} for {p.title}.")
                return redirect("ballot", election_id=election.id)
            if not picked and not p.allow_abstain:
                messages.error(request, f"Please choose a candidate for {p.title}.")
                return redirect("ballot", election_id=election.id)
            summary.append({"position": p, "picked": picked})
        return render(request, "voting/review.html", {"election": election, "summary": summary})

    return render(request, "voting/ballot.html", {"election": election, "positions": positions})


@student_required
def receipt(request, election_id):
    election = get_object_or_404(Election, pk=election_id)
    record = VoterRecord.objects.filter(election=election, student=request.student).first()
    if not record:
        return redirect("home")
    return render(request, "voting/receipt.html", {"election": election, "record": record})


# ------------------------------------------------------------------ staff
def audit_check(request):
    if not (request.user.is_authenticated and request.user.is_staff):
        raise Http404
    ok, bad_id = services.verify_audit_chain()
    return JsonResponse({"intact": ok, "first_broken_entry": bad_id})


# ------------------------------------------------------------------ error pages
def _err(request, template, status):
    return render(request, template, status=status)


def error_400(request, exception=None): return _err(request, "errors/400.html", 400)
def error_403(request, exception=None): return _err(request, "errors/403.html", 403)
def error_404(request, exception=None): return _err(request, "errors/404.html", 404)
def error_500(request):
    from django.http import HttpResponseServerError
    from django.template import loader
    return HttpResponseServerError(loader.get_template("errors/500.html").render({}))

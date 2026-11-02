"""Security layer: client IP, rate limiting, the /admin trap, IP blocks and security headers."""
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.cache import add_never_cache_headers

# URL segments that only attackers/scanners (or curious people) try. The REAL admin address is secret.
TRAP_SEGMENTS = {"admin", "administrator", "wp-admin", "wp-login.php", "phpmyadmin", "adminer", "dbadmin"}
# Pages blocked for flagged devices. /login-help/ stays open so a wrongly blocked student can appeal.
BLOCKED_PREFIXES = ("/login/", "/vote/", "/check/")
SENSITIVE_PREFIXES = ("/login", "/vote", "/check", "/login-help")

CSP_PUBLIC = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
              "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
              "form-action 'self'; frame-ancestors 'none'")
# Django's admin needs a few inline styles/scripts, so it gets a slightly looser (still locked-down) policy.
CSP_ADMIN = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
             "img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def get_client_ip(request):
    if settings.BEHIND_PROXY:
        xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if xff:
            return xff.split(",")[-1].strip()      # last hop = the address our own proxy saw
    return request.META.get("REMOTE_ADDR", "0.0.0.0")


# ------------------------------------------------------------ rate limiting
def hit(key, window=None):
    window = window or settings.RATE_WINDOW_SECONDS
    cache.add(key, 0, window)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        return 1


def count(key):
    return cache.get(key, 0)


def reset(key):
    cache.delete(key)


# ------------------------------------------------------------ intrusion handling
def record_intrusion(request, path):
    """Someone requested a trap URL: block the device and blacklist the student (if logged in)."""
    from . import services
    from .models import BlockedIP, Student
    ip = request.client_ip
    student = None
    pk = request.session.get("student_pk")
    if pk:
        student = Student.objects.filter(pk=pk).first()
    BlockedIP.objects.update_or_create(ip=ip, defaults={
        "path": path[:200], "user_agent": request.META.get("HTTP_USER_AGENT", "")[:200],
        "student_id": student.student_id if student else "", "reason": "Requested restricted admin URL"})
    if student and not student.is_blacklisted:
        student.is_blacklisted = True
        student.blacklist_reason = f"Tried to open restricted URL {path[:100]}"
        student.save(update_fields=["is_blacklisted", "blacklist_reason"])
    request.session["blocked"] = True
    request.session.pop("student_pk", None)
    services.log("INTRUSION_BLOCKED", actor=ip, detail=f"path={path[:150]} student={student.student_id if student else '-'}")


def is_device_blocked(request):
    from .models import BlockedIP
    if request.session.get("blocked"):
        return True
    return settings.BLOCK_BY_IP and BlockedIP.objects.filter(ip=request.client_ip).exists()


class GuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.admin_prefix = "/" + settings.ADMIN_URL
        self.admin_login = self.admin_prefix + "login/"

    def __call__(self, request):
        request.client_ip = get_client_ip(request)
        path = request.path_info
        lower = path.lower()

        is_admin_area = path.startswith(self.admin_prefix)
        is_asset = lower.startswith(("/static/", "/media/"))

        # 1) trap: /admin, /wp-admin, /phpmyadmin ... (never triggered by staff, assets or the real admin)
        if not is_admin_area and not is_asset:
            segments = {s for s in lower.split("/") if s}
            if segments & TRAP_SEGMENTS and not (request.user.is_authenticated and request.user.is_staff):
                record_intrusion(request, path)
                return render(request, "blocked.html", {"reason": "trap"}, status=403)

        # 2) flagged devices cannot log in or vote
        if lower.startswith(BLOCKED_PREFIXES) and is_device_blocked(request):
            return render(request, "blocked.html", {"reason": "flagged"}, status=403)

        # 3) brute-force protection for the real admin login
        key = f"adminfail:{request.client_ip}"
        if path == self.admin_login and request.method == "POST" and count(key) >= settings.ADMIN_LOGIN_LIMIT:
            return HttpResponse("Too many failed attempts. Try again later.", status=429, content_type="text/plain")

        response = self.get_response(request)

        if path == self.admin_login and request.method == "POST":
            if response.status_code == 200:       # form shown again = wrong credentials
                hit(key)
            elif response.status_code == 302:
                reset(key)

        response["Content-Security-Policy"] = CSP_ADMIN if is_admin_area else CSP_PUBLIC
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        response["Cross-Origin-Resource-Policy"] = "same-origin"
        if lower.startswith(SENSITIVE_PREFIXES) or is_admin_area:
            add_never_cache_headers(response)
        return response

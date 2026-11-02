import io
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import (AuditLog, BlockedIP, Candidate, CandidateProgram, Election, LoginIssue, Position,
                     SiteSettings, Student, Vote, VoterRecord, normalize_name)

ADMIN = "/" + settings.ADMIN_URL          # e.g. /dimtmwelection/


def mk(sid, name, dept="Computing"):
    return Student.objects.create(student_id=sid, full_name=name, department=dept)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        now = timezone.now()
        self.e = Election.objects.create(title="T", start_time=now - timedelta(hours=1),
                                         end_time=now + timedelta(hours=5), visibility=Election.LIVE)
        self.p = Position.objects.create(election=self.e, title="Chair")
        self.a = Candidate.objects.create(position=self.p, name="Alice")
        self.b = Candidate.objects.create(position=self.p, name="Bob")
        CandidateProgram.objects.create(candidate=self.a, title="Free Wi-Fi", details="In every hostel")
        self.s1 = mk("DIMT001", "Chikondi Banda")
        self.s2 = mk("DIMT002", "Thoko Phiri")

    def login(self, sid="DIMT001", name="Chikondi Banda"):
        return self.client.post(reverse("login"), {"student_id": sid, "full_name": name})


class LoginTests(Base):
    def test_login_with_name_and_id(self):
        r = self.login()
        self.assertRedirects(r, reverse("home"))
        self.assertEqual(self.client.session["student_pk"], self.s1.pk)

    def test_name_is_forgiving_about_case_order_spaces(self):
        self.assertEqual(normalize_name("  banda,  CHIKONDI "), normalize_name("Chikondi Banda"))
        self.login("dimt 001", "BANDA chikondi")
        self.assertIn("student_pk", self.client.session)

    def test_wrong_name_or_id_rejected_with_same_message(self):
        r1 = self.login("DIMT001", "Wrong Person")
        r2 = self.login("NOPE999", "Chikondi Banda")
        for r in (r1, r2):
            self.assertNotIn("student_pk", self.client.session)
            self.assertContains(r, "could not match")
            self.assertContains(r, "Send my ID to the error handler")

    def test_failed_login_links_to_error_handler_with_id(self):
        r = self.login("DIMT001", "Wrong")
        self.assertContains(r, reverse("login_help") + "?student_id=DIMT001")

    def test_student_lockout_after_five_failures(self):
        for _ in range(5):
            self.login("DIMT001", "Nope Nope")
        self.s1.refresh_from_db()
        self.assertTrue(self.s1.is_locked)
        self.login()                                    # right details, still locked
        self.assertNotIn("student_pk", self.client.session)

    def test_device_limit_blocks_guessing_many_ids(self):
        for i in range(settings.LOGIN_IP_LIMIT):
            self.login(f"X{i:03d}", "Guess Guess")
        r = self.login()
        self.assertEqual(r.status_code, 429)

    def test_ineligible_and_blacklisted_cannot_login(self):
        self.s1.is_eligible = False; self.s1.save()
        self.login(); self.assertNotIn("student_pk", self.client.session)
        self.s1.is_eligible = True; self.s1.is_blacklisted = True; self.s1.save()
        self.login(); self.assertNotIn("student_pk", self.client.session)

    def test_session_cleared_if_blacklisted_while_logged_in(self):
        self.login()
        self.s1.is_blacklisted = True; self.s1.save()
        r = self.client.get(reverse("ballot", args=[self.e.id]))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r["Location"])

    def test_next_redirect_is_safe(self):
        r = self.client.post(reverse("login") + "?next=https://evil.example/", {"student_id": "DIMT001", "full_name": "Chikondi Banda"})
        self.assertRedirects(r, reverse("home"))


class AdminTrapTests(Base):
    def test_admin_path_is_blocked_and_ip_blacklisted(self):
        r = self.client.get("/admin/")
        self.assertEqual(r.status_code, 403)
        self.assertTrue(BlockedIP.objects.filter(ip="127.0.0.1").exists())
        self.assertEqual(self.login().status_code, 403)               # cannot log in any more
        self.assertEqual(self.client.get(reverse("results")).status_code, 200)   # public results still visible
        self.assertEqual(self.client.get(reverse("login_help")).status_code, 200)  # can still appeal

    def test_variants_are_trapped(self):
        for path in ["/admin", "/Admin/login/", "/wp-admin/", "/phpmyadmin/", "/x/../admin/"]:
            BlockedIP.objects.all().delete()
            self.assertEqual(self.client.get(path).status_code, 403, path)

    def test_logged_in_student_is_blacklisted_for_voting(self):
        self.login()
        self.client.get("/admin/")
        self.s1.refresh_from_db()
        self.assertTrue(self.s1.is_blacklisted)
        self.assertTrue(BlockedIP.objects.get(ip="127.0.0.1").student_id, "DIMT001")
        with self.assertRaises(services.VoteError):
            services.cast_ballot(self.s1, self.e, {self.p.id: [self.a.id]})

    def test_flagged_browser_stays_blocked_even_if_ip_changes(self):
        self.client.get("/admin/")
        BlockedIP.objects.all().delete()                  # even if IP changes, the session cookie is flagged
        r = self.login()
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("student_pk", self.client.session)

    def test_real_admin_url_works_and_does_not_trigger_trap(self):
        r = self.client.get(ADMIN)
        self.assertIn(r.status_code, (200, 302))
        self.assertFalse(BlockedIP.objects.exists())
        r = self.client.get("/static/admin/css/base.css")  # admin's own assets contain 'admin' in the path
        self.assertFalse(BlockedIP.objects.exists())

    def test_staff_typing_admin_is_not_blocked(self):
        User.objects.create_superuser("boss", "b@x.com", "very-long-pass-123")
        self.client.login(username="boss", password="very-long-pass-123")
        self.client.get("/admin/")
        self.assertFalse(BlockedIP.objects.exists())

    def test_admin_login_bruteforce_limited(self):
        url = ADMIN + "login/"
        for _ in range(settings.ADMIN_LOGIN_LIMIT):
            self.client.post(url, {"username": "boss", "password": "bad"})
        self.assertEqual(self.client.post(url, {"username": "boss", "password": "bad"}).status_code, 429)

    def test_ip_blocking_can_be_switched_off_for_shared_networks(self):
        from django.test import Client, override_settings
        self.client.get("/admin/")                         # prober is flagged
        with override_settings(BLOCK_BY_IP=False):
            other = Client()                               # a different student, same public IP, fresh cookies
            r = other.post(reverse("login"), {"student_id": "DIMT002", "full_name": "Thoko Phiri"})
            self.assertEqual(r.status_code, 302)
            self.assertIn("student_pk", other.session)
            self.assertEqual(self.client.get(reverse("login")).status_code, 403)   # but the prober's browser stays blocked

    def test_unblock_command(self):
        from django.core.management import call_command
        self.client.get("/admin/")
        call_command("unblock_ip", "127.0.0.1", stdout=io.StringIO())
        self.assertFalse(BlockedIP.objects.exists())


class ErrorHandlerTests(Base):
    def test_help_page_saves_id_for_admin(self):
        r = self.client.post(reverse("login_help"), {"student_id": "dimt 001", "full_name": "Chikondi", "problem": "name_mismatch", "message": "help"})
        self.assertContains(r, "HELP-")
        i = LoginIssue.objects.get()
        self.assertEqual((i.student_id_entered, i.problem), ("DIMT001", "name_mismatch"))

    def test_help_requires_id_and_resists_spam(self):
        self.client.post(reverse("login_help"), {"student_id": "", "problem": "other"})
        self.assertEqual(LoginIssue.objects.count(), 0)
        self.client.post(reverse("login_help"), {"student_id": "DIMT009", "website": "http://spam"})   # bot trap field
        self.assertEqual(LoginIssue.objects.count(), 0)
        for _ in range(7):
            self.client.post(reverse("login_help"), {"student_id": "DIMT003", "problem": "other"})
        self.assertEqual(LoginIssue.objects.count(), 5)

    def test_custom_404(self):
        r = self.client.get("/does-not-exist/")
        self.assertContains(r, "Page not found", status_code=404)


class RegistrationCheckTests(Base):
    def test_listed_id_shows_masked_name_only(self):
        r = self.client.post(reverse("check"), {"student_id": "dimt001"})
        self.assertContains(r, "is on the list")
        self.assertContains(r, "C******* B****")
        self.assertNotContains(r, "Chikondi")

    def test_unlisted_id(self):
        self.assertContains(self.client.post(reverse("check"), {"student_id": "ZZZ1"}), "is not on the list")


class RollUploadTests(Base):
    def up(self, text, **kw):
        return services.import_students(io.BytesIO(text.encode()), **kw)

    def test_import_create_update_and_errors(self):
        r = self.up("student_id,full_name,department\nDIMT100,Mary Banda,Business\nDIMT001,Chikondi B. Banda,Computing\n,,\nBAD ID!!,X Y,\nDIMT100,Dup Row,\nDIMT101,,\n")
        self.assertEqual((r["created"], r["updated"], r["skipped"]), (1, 1, 3))
        self.assertTrue(Student.objects.filter(student_id="DIMT100").exists())
        self.s1.refresh_from_db(); self.assertEqual(self.s1.full_name, "Chikondi B. Banda")

    def test_first_last_name_columns_and_missing_columns(self):
        self.up("ID Number,First Name,Last Name\nA1B,Tom,Mwale\n")
        self.assertEqual(Student.objects.get(student_id="A1B").full_name, "Tom Mwale")
        self.assertTrue(self.up("foo,bar\n1,2\n")["errors"])

    def test_mark_missing_ineligible(self):
        self.up("student_id,full_name\nDIMT001,Chikondi Banda\n", mark_missing_ineligible=True)
        self.s2.refresh_from_db(); self.assertFalse(self.s2.is_eligible)

    def test_admin_upload_page_and_only_staff(self):
        url = reverse("admin:voting_student_import")
        self.assertEqual(self.client.get(url).status_code, 302)       # anonymous -> admin login
        User.objects.create_superuser("boss", "b@x.com", "very-long-pass-123")
        self.client.login(username="boss", password="very-long-pass-123")
        self.assertEqual(self.client.get(url).status_code, 200)
        f = SimpleUploadedFile("list.csv", b"student_id,full_name\nNEW1,New Person\n")
        r = self.client.post(url, {"file": f})
        self.assertContains(r, "Added 1")
        self.assertTrue(Student.objects.filter(student_id="NEW1").exists())
        bad = SimpleUploadedFile("evil.exe", b"x")
        self.assertContains(self.client.post(url, {"file": bad}), "upload a .csv")


class VotingTests(Base):
    def test_vote_counts_and_no_link_to_student(self):
        services.cast_ballot(self.s1, self.e, {self.p.id: [self.a.id]})
        self.assertEqual(Vote.objects.filter(candidate=self.a).count(), 1)
        self.assertNotIn("student", [f.name for f in Vote._meta.get_fields()])

    def test_double_vote_blocked(self):
        services.cast_ballot(self.s1, self.e, {self.p.id: [self.a.id]})
        with self.assertRaises(services.VoteError):
            services.cast_ballot(self.s1, self.e, {self.p.id: [self.b.id]})
        self.assertEqual(Vote.objects.count(), 1)

    def test_invalid_rolls_back_and_closed_rejected(self):
        with self.assertRaises(services.VoteError):
            services.cast_ballot(self.s1, self.e, {self.p.id: [9999]})
        self.assertEqual(VoterRecord.objects.count(), 0)
        self.e.end_time = timezone.now() - timedelta(minutes=1); self.e.save()
        with self.assertRaises(services.VoteError):
            services.cast_ballot(self.s1, self.e, {self.p.id: [self.a.id]})

    def test_full_http_flow_and_programmes_shown(self):
        self.login("DIMT002", "Thoko Phiri")
        r = self.client.get(reverse("ballot", args=[self.e.id]))
        self.assertContains(r, "View programmes (1)"); self.assertContains(r, "Free Wi-Fi")
        r = self.client.post(reverse("ballot", args=[self.e.id]), {"step": "review", f"pos_{self.p.id}": self.b.id})
        self.assertContains(r, "Confirm your vote")
        r = self.client.post(reverse("ballot", args=[self.e.id]), {"step": "confirm", f"pos_{self.p.id}": self.b.id})
        self.assertRedirects(r, reverse("receipt", args=[self.e.id]))
        self.assertEqual(Vote.objects.filter(candidate=self.b).count(), 1)

    def test_ballot_needs_login(self):
        self.assertEqual(self.client.get(reverse("ballot", args=[self.e.id])).status_code, 302)

    def test_public_candidates_page_lists_programmes_without_login(self):
        r = self.client.get(reverse("candidates_for", args=[self.e.id]))
        self.assertContains(r, "Free Wi-Fi"); self.assertContains(r, "In every hostel")


class ResultsTests(Base):
    def test_public_dashboard_no_login_and_no_identity_leak(self):
        services.cast_ballot(self.s1, self.e, {self.p.id: [self.a.id]})
        self.assertEqual(self.client.get(reverse("results")).status_code, 200)
        data = self.client.get(reverse("results_api", args=[self.e.id])).json()
        top = data["positions"][0]["candidates"][0]
        self.assertEqual((top["name"], top["votes"]), ("Alice", 1))
        self.assertNotIn("DIMT001", str(data)); self.assertNotIn("Chikondi", str(data))

    def test_visibility_kill_switch_min_votes(self):
        services.cast_ballot(self.s1, self.e, {self.p.id: [self.a.id]})
        self.e.visibility = Election.TURNOUT; self.e.save()
        d = services.public_results(self.e); self.assertEqual((d["mode"], d["positions"]), ("turnout", []))
        self.e.visibility = Election.LIVE; self.e.min_votes_to_show = 5; self.e.save()
        self.assertTrue(services.public_results(self.e)["positions"][0]["withheld"])
        self.e.hide_results_now = True; self.e.save()
        self.assertEqual(services.public_results(self.e)["mode"], "hidden")

    def test_audit_chain_detects_tampering(self):
        services.log("A"); services.log("B"); services.log("C")
        self.assertTrue(services.verify_audit_chain()[0])
        AuditLog.objects.filter(action="B").update(detail="tampered")
        self.assertFalse(services.verify_audit_chain()[0])


class SiteAndSecurityTests(Base):
    def test_background_css_follows_admin_settings(self):
        s = SiteSettings.load(); s.background_color = "#102030"; s.save()
        css = self.client.get(reverse("theme_css")).content.decode()
        self.assertIn("#102030", css); self.assertNotIn("url(", css)

    def test_background_image_in_css(self):
        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", (10, 10), "red").save(buf, "PNG")
        s = SiteSettings.load(); s.background_image.save("bg.png", io.BytesIO(buf.getvalue()), save=True)
        css = self.client.get(reverse("theme_css")).content.decode()
        self.assertIn("/media/site/", css)
        s.background_image.delete()

    def test_security_headers(self):
        r = self.client.get(reverse("home"))
        self.assertIn("script-src 'self'", r["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", r["Content-Security-Policy"].split("script-src")[1].split(";")[0])
        self.assertEqual(r["X-Frame-Options"], "DENY")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", self.client.get(reverse("login"))["Cache-Control"])

    def test_pages_have_no_inline_scripts_or_handlers(self):
        for name in ("home", "login", "login_help", "check", "results", "candidates"):
            html = self.client.get(reverse(name)).content.decode()
            self.assertNotIn("onclick=", html); self.assertNotIn("onchange=", html)
            self.assertNotRegex(html, r"<script(?![^>]*src=)")

    def test_xss_in_names_is_escaped(self):
        Candidate.objects.create(position=self.p, name="<script>alert(1)</script>")
        html = self.client.get(reverse("candidates_for", args=[self.e.id])).content.decode()
        self.assertNotIn("<script>alert(1)", html)


class HeaderTests(Base):
    def test_header_button_changes_with_state(self):
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn("Login to vote", html)
        self.login()
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn(">Vote now<", html.split("</header>")[0])
        services.cast_ballot(self.s1, self.e, {self.p.id: [self.a.id]})
        self.assertIn("Voted", self.client.get(reverse("home")).content.decode().split("</header>")[0])

    def test_no_open_election_shows_plain_login(self):
        self.e.end_time = timezone.now() - timedelta(minutes=1); self.e.save()
        head = self.client.get(reverse("home")).content.decode().split("</header>")[0]
        self.assertNotIn("Login to vote", head); self.assertIn("Student login", head)

    def test_menu_button_and_sticky_css_present(self):
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn('id="menu-btn"', html); self.assertIn('id="main-nav"', html)
        css = open(settings.BASE_DIR / "static/css/style.css").read()
        self.assertIn("position:sticky", css)

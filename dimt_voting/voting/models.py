import re
import secrets
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, RegexValidator
from django.db import models
from django.utils import timezone


# ------------------------------------------------------------------ helpers
def normalize_id(value):
    """'dimt 001 ' -> 'DIMT001'"""
    return re.sub(r"\s+", "", value or "").upper()


def normalize_name(value):
    """Case, accents, punctuation and word ORDER are ignored: 'banda,  Chikondi' == 'Chikondi Banda'."""
    s = unicodedata.normalize("NFKD", value or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = s.replace("'", "").replace("\u2019", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(sorted(s.split()))


def mask_name(value):
    """'Chikondi Banda' -> 'C******* B****'  (lets a student confirm their entry without exposing the full name)"""
    return " ".join(w[0] + "*" * (len(w) - 1) for w in (value or "").split())


def new_receipt():
    return secrets.token_hex(5).upper()


def validate_image_size(f):
    if f.size > settings.MAX_IMAGE_BYTES:
        raise ValidationError("Image is too large (maximum 5 MB).")


# ------------------------------------------------------------------ site appearance (admin controlled)
class SiteSettings(models.Model):
    site_name = models.CharField(max_length=80, default="DIMT Voting")
    background_image = models.ImageField("Website background image", upload_to="site/", blank=True,
                                         validators=[validate_image_size],
                                         help_text="Shown behind the whole website. Wide photos (1600px+) look best. Max 5 MB.")
    background_color = models.CharField("Background colour", max_length=7, default="#f6f8f4",
                                        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Use a hex colour such as #f6f8f4")])
    fade_percent = models.PositiveSmallIntegerField(
        "Fade image (%)", default=55, validators=[MaxValueValidator(95)],
        help_text="How much the background colour covers the image so text stays readable (0 = full image, 95 = almost hidden).")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Website appearance"
        verbose_name_plural = "Website appearance"

    def __str__(self):
        return "Website appearance"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ------------------------------------------------------------------ students (the voter roll, uploaded by admin)
class Student(models.Model):
    student_id = models.CharField("ID number", max_length=40, unique=True)
    full_name = models.CharField(max_length=150)
    name_key = models.CharField(max_length=200, db_index=True, editable=False)
    department = models.CharField("Department / Class", max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    is_eligible = models.BooleanField(default=True, help_text="Untick to stop this student from voting")
    is_blacklisted = models.BooleanField(default=False, help_text="Blocked from logging in and voting")
    blacklist_reason = models.CharField(max_length=200, blank=True)
    failed_logins = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["student_id"]

    def save(self, *args, **kwargs):
        self.student_id = normalize_id(self.student_id)
        self.full_name = " ".join(self.full_name.split())
        self.name_key = normalize_name(self.full_name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student_id} - {self.full_name}"

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())

    def name_matches(self, typed):
        return bool(typed) and normalize_name(typed) == self.name_key


# ------------------------------------------------------------------ elections
class Election(models.Model):
    LIVE, TURNOUT, HIDDEN = "live", "turnout", "hidden"
    VISIBILITY = [
        (LIVE, "Live results (candidate counts shown while voting)"),
        (TURNOUT, "Turnout only (candidate counts hidden until polls close)"),
        (HIDDEN, "Hidden until close (nothing shown until polls close)"),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    visibility = models.CharField(max_length=10, choices=VISIBILITY, default=TURNOUT)
    min_votes_to_show = models.PositiveIntegerField(
        default=0, help_text="Hide candidate counts until a position has at least this many votes")
    hide_results_now = models.BooleanField(default=False, help_text="KILL SWITCH: instantly hide all public results")
    is_certified = models.BooleanField(default=False, help_text="Tick when results are officially certified")
    is_active = models.BooleanField(default=True, help_text="Only active elections appear to students")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_time"]

    def __str__(self):
        return self.title

    @property
    def status(self):
        now = timezone.now()
        if now < self.start_time:
            return "upcoming"
        if now <= self.end_time:
            return "open"
        return "certified" if self.is_certified else "closed"

    @property
    def is_open(self):
        return self.status == "open"

    @property
    def has_closed(self):
        return timezone.now() > self.end_time


class Position(models.Model):
    election = models.ForeignKey(Election, on_delete=models.CASCADE, related_name="positions")
    title = models.CharField(max_length=120)
    order = models.PositiveSmallIntegerField(default=0)
    max_choices = models.PositiveSmallIntegerField(default=1, help_text="How many candidates a student may choose")
    allow_abstain = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.title} ({self.election})"


class Candidate(models.Model):
    position = models.ForeignKey(Position, on_delete=models.CASCADE, related_name="candidates")
    name = models.CharField(max_length=150)
    department = models.CharField("Department / Class", max_length=100, blank=True)
    photo = models.ImageField(upload_to="candidates/", blank=True, validators=[validate_image_size])
    manifesto = models.TextField("Short statement / slogan", blank=True)
    is_approved = models.BooleanField(default=True, help_text="Only approved candidates appear on the ballot")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} - {self.position.title}"


class CandidateProgram(models.Model):
    """One promise / programme item in a candidate's campaign plan."""
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="programs")
    title = models.CharField("Programme", max_length=150)
    details = models.TextField("Details", blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Programme"
        verbose_name_plural = "Programmes"

    def __str__(self):
        return self.title


# ------------------------------------------------------------------ ballots
class VoterRecord(models.Model):
    """WHO has voted (turnout). Deliberately holds NO link to what they chose."""
    election = models.ForeignKey(Election, on_delete=models.CASCADE, related_name="voter_records")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="voter_records")
    receipt = models.CharField(max_length=20, default=new_receipt, unique=True)
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["election", "student"], name="one_ballot_per_student")]

    def __str__(self):
        return f"{self.student.student_id} voted in {self.election}"


class Vote(models.Model):
    """WHAT was chosen. Holds NO link to the student. candidate=None means abstain.
    cast_hour is rounded to the hour so a vote cannot be matched to a voter by time."""
    position = models.ForeignKey(Position, on_delete=models.CASCADE, related_name="votes")
    candidate = models.ForeignKey(Candidate, null=True, blank=True, on_delete=models.CASCADE, related_name="votes")
    cast_hour = models.DateTimeField()


# ------------------------------------------------------------------ support & security
class LoginIssue(models.Model):
    """Reports sent through the 'error handler' page when a student cannot log in."""
    PROBLEMS = [
        ("not_found", "My ID number says it is not on the list"),
        ("name_mismatch", "My name does not match"),
        ("locked", "My account is locked"),
        ("blocked", "I was blocked by mistake"),
        ("other", "Something else"),
    ]
    NEW, RESOLVED = "new", "resolved"

    student_id_entered = models.CharField("ID number sent", max_length=40)
    name_entered = models.CharField(max_length=150, blank=True)
    problem = models.CharField(max_length=20, choices=PROBLEMS, default="other")
    message = models.TextField(max_length=600, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    ip = models.CharField(max_length=45, blank=True)
    status = models.CharField(max_length=10, choices=[(NEW, "New"), (RESOLVED, "Resolved")], default=NEW)
    admin_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def reference(self):
        return f"HELP-{self.pk:05d}"

    def __str__(self):
        return f"{self.reference} ({self.student_id_entered})"


class BlockedIP(models.Model):
    ip = models.CharField("IP address", max_length=45, unique=True)
    path = models.CharField("Address they tried", max_length=200, blank=True)
    student_id = models.CharField("Student (if logged in)", max_length=40, blank=True)
    reason = models.CharField(max_length=200, blank=True)
    user_agent = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Blocked device"
        verbose_name_plural = "Blocked devices"

    def __str__(self):
        return self.ip


class AuditLog(models.Model):
    """Hash-chained log: each row's hash covers the previous row's hash, so tampering is detectable."""
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=100, blank=True)
    action = models.CharField(max_length=60)
    detail = models.CharField(max_length=300, blank=True)
    prev_hash = models.CharField(max_length=64, blank=True)
    hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"

from django import forms
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from . import services
from .models import (AuditLog, BlockedIP, Candidate, CandidateProgram, Election, LoginIssue, Position,
                     SiteSettings, Student, Vote, VoterRecord, normalize_id)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


# ------------------------------------------------------------------ website appearance
@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    fields = ("site_name", "background_image", "preview", "background_color", "fade_percent")
    readonly_fields = ("preview",)

    @admin.display(description="Current background")
    def preview(self, obj):
        if obj and obj.background_image:
            return format_html('<img src="{}" style="max-width:320px;border-radius:8px;border:1px solid #ccc">', obj.background_image.url)
        return "No image uploaded. The plain background colour is used."

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        SiteSettings.load()
        obj = SiteSettings.objects.first()
        return redirect(reverse("admin:voting_sitesettings_change", args=[obj.pk]))

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        services.log("SITE_APPEARANCE_CHANGED", request.user.username)


# ------------------------------------------------------------------ voter roll
class RollUploadForm(forms.Form):
    file = forms.FileField(label="CSV file")
    mark_missing = forms.BooleanField(
        required=False, label="Mark students who are NOT in this file as not eligible",
        help_text="Use when this file is the complete, up-to-date list.")

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > settings.MAX_ROLL_UPLOAD_BYTES:
            raise forms.ValidationError("File is too large (maximum 3 MB).")
        if not f.name.lower().endswith(".csv"):
            raise forms.ValidationError("Please upload a .csv file (in Excel: File > Save As > CSV).")
        return f


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("student_id", "full_name", "department", "is_eligible", "is_blacklisted", "failed_logins")
    list_filter = ("is_eligible", "is_blacklisted", "department")
    search_fields = ("student_id", "full_name")
    change_list_template = "admin/voting/student/change_list.html"
    actions = ["unlock", "mark_eligible", "mark_ineligible", "blacklist", "unblacklist"]

    def get_urls(self):
        return [path("import-csv/", self.admin_site.admin_view(self.import_view), name="voting_student_import")] + super().get_urls()

    def import_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        result = None
        if request.method == "POST":
            form = RollUploadForm(request.POST, request.FILES)
            if form.is_valid():
                result = services.import_students(form.cleaned_data["file"], form.cleaned_data["mark_missing"])
        else:
            form = RollUploadForm()
        ctx = dict(self.admin_site.each_context(request), form=form, result=result, title="Upload student list",
                   opts=self.model._meta)
        return render(request, "admin/voting/student/import_csv.html", ctx)

    @admin.action(description="Unlock selected students")
    def unlock(self, request, qs): qs.update(failed_logins=0, locked_until=None)

    @admin.action(description="Mark selected as eligible")
    def mark_eligible(self, request, qs): qs.update(is_eligible=True)

    @admin.action(description="Mark selected as NOT eligible")
    def mark_ineligible(self, request, qs): qs.update(is_eligible=False)

    @admin.action(description="Blacklist selected (block login and voting)")
    def blacklist(self, request, qs):
        qs.update(is_blacklisted=True, blacklist_reason="Blacklisted by election officer")

    @admin.action(description="Remove blacklist from selected")
    def unblacklist(self, request, qs):
        ids = list(qs.values_list("student_id", flat=True))
        qs.update(is_blacklisted=False, blacklist_reason="", failed_logins=0, locked_until=None)
        BlockedIP.objects.filter(student_id__in=ids).delete()
        services.log("UNBLACKLISTED", request.user.username, ", ".join(ids)[:250])


# ------------------------------------------------------------------ elections, candidates, programmes
class PositionInline(admin.TabularInline):
    model = Position
    extra = 1


@admin.register(Election)
class ElectionAdmin(admin.ModelAdmin):
    list_display = ("title", "start_time", "end_time", "status", "visibility", "hide_results_now", "is_certified")
    list_filter = ("visibility", "is_certified", "is_active")
    inlines = [PositionInline]
    actions = ["kill_switch_on", "kill_switch_off", "certify"]

    @admin.action(description="KILL SWITCH ON: hide public results now")
    def kill_switch_on(self, request, qs):
        qs.update(hide_results_now=True)
        services.log("KILL_SWITCH_ON", request.user.username, ", ".join(qs.values_list("title", flat=True)))

    @admin.action(description="Kill switch OFF: show public results again")
    def kill_switch_off(self, request, qs):
        qs.update(hide_results_now=False)
        services.log("KILL_SWITCH_OFF", request.user.username, ", ".join(qs.values_list("title", flat=True)))

    @admin.action(description="Certify results (removes 'Provisional' label)")
    def certify(self, request, qs):
        qs.update(is_certified=True)
        services.log("CERTIFIED", request.user.username, ", ".join(qs.values_list("title", flat=True)))

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        services.log("ELECTION_SAVED", request.user.username, obj.title)


class CandidateInline(admin.StackedInline):
    model = Candidate
    extra = 1
    show_change_link = True


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ("title", "election", "max_choices", "allow_abstain")
    list_filter = ("election",)
    inlines = [CandidateInline]


class ProgramInline(admin.StackedInline):
    model = CandidateProgram
    extra = 3
    fields = ("order", "title", "details")


@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = ("name", "position", "department", "programme_count", "is_approved")
    list_filter = ("position__election", "is_approved")
    search_fields = ("name",)
    inlines = [ProgramInline]

    @admin.display(description="Programmes")
    def programme_count(self, obj):
        return obj.programs.count()


# ------------------------------------------------------------------ support & security
@admin.register(LoginIssue)
class LoginIssueAdmin(admin.ModelAdmin):
    list_display = ("reference", "student_id_entered", "roll_status", "problem", "status", "created_at")
    list_filter = ("status", "problem")
    search_fields = ("student_id_entered", "name_entered")
    readonly_fields = ("student_id_entered", "name_entered", "problem", "message", "phone", "ip", "created_at", "roll_status")
    fields = ("student_id_entered", "name_entered", "roll_status", "problem", "message", "phone", "ip",
              "created_at", "status", "admin_note")
    actions = ["mark_resolved"]

    @admin.display(description="Found on student list?")
    def roll_status(self, obj):
        s = Student.objects.filter(student_id=normalize_id(obj.student_id_entered)).first()
        if not s:
            return "NOT on the list: add this student or tell them the ID is wrong"
        flags = []
        if s.is_blacklisted: flags.append("BLACKLISTED")
        if not s.is_eligible: flags.append("not eligible")
        if s.is_locked: flags.append("locked")
        return f"Yes: registered as '{s.full_name}' ({s.department or 'no department'})" + (f" [{', '.join(flags)}]" if flags else "")

    @admin.action(description="Mark selected as resolved")
    def mark_resolved(self, request, qs): qs.update(status=LoginIssue.RESOLVED)

    def has_add_permission(self, request): return False


@admin.register(BlockedIP)
class BlockedIPAdmin(admin.ModelAdmin):
    list_display = ("ip", "path", "student_id", "reason", "created_at")
    search_fields = ("ip", "student_id")
    readonly_fields = ("created_at",)
    # Deleting a row here unblocks that device.


@admin.register(VoterRecord)
class VoterRecordAdmin(ReadOnlyAdmin):
    list_display = ("election", "student", "receipt", "voted_at")
    list_filter = ("election",)


@admin.register(Vote)
class VoteAdmin(ReadOnlyAdmin):
    list_display = ("position", "candidate", "cast_hour")
    list_filter = ("position__election",)


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "actor", "action", "detail", "hash")
    list_filter = ("action",)

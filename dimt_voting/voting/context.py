from .models import Election, SiteSettings, Student, VoterRecord


def site(request):
    student = None
    pk = request.session.get("student_pk") if hasattr(request, "session") else None
    if pk:
        student = Student.objects.filter(pk=pk, is_blacklisted=False).first()
    elections = list(Election.objects.filter(is_active=True))
    nav_open = next((e for e in elections if e.is_open), None)       # election currently accepting votes
    return {
        "site": SiteSettings.load(),
        "current_student": student,
        "nav_election": elections[0] if elections else None,
        "nav_open": nav_open,
        "nav_voted": bool(student and nav_open and VoterRecord.objects.filter(student=student, election=nav_open).exists()),
    }

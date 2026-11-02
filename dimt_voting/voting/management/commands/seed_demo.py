import random
import secrets
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from voting import services
from voting.models import Candidate, CandidateProgram, Election, Position, Student

DEPTS = ["Computing", "Business", "Education", "Engineering", "Administration"]
FIRST = ["Chikondi", "Thoko", "Mphatso", "Yamikani", "Limbani", "Tadala", "Chisomo", "Wongani", "Dalitso", "Kondwani"]
LAST = ["Banda", "Phiri", "Mwale", "Chirwa", "Kachingwe", "Mtambo", "Nyirenda", "Gondwe", "Msiska", "Kalua"]

PROGRAMS = {
    "Chikondi Banda": [("Free Wi-Fi in all hostels", "Work with management to extend campus Wi-Fi to every hostel block."),
                       ("Student welfare fund", "Create an emergency fund for students facing sudden hardship.")],
    "Thoko Phiri": [("Better library hours", "Open the library until 10 pm during exams.")],
    "Limbani Mwale": [("Sports and clubs", "Support every club with a yearly budget and a transparent report.")],
    "Tadala Chirwa": [("Open union meetings", "Publish minutes of every union meeting within 3 days.")],
    "Dalitso Gondwe": [("Transparent finances", "Publish a monthly income and spending summary for all students.")],
}


class Command(BaseCommand):
    help = "Create demo students, an open election, candidates with programmes, and some sample votes."

    def handle(self, *args, **opts):
        random.seed(7)
        if not User.objects.filter(username="admin").exists():
            pw = secrets.token_urlsafe(12)
            User.objects.create_superuser("admin", "admin@dimt.local", pw)
            self.stdout.write(self.style.WARNING(f"Admin account created:  username 'admin'   password '{pw}'   (write it down!)"))

        students = []
        for i in range(1, 61):
            sid = f"DIMT{i:03d}"
            name = f"{FIRST[i % 10]} {LAST[(i // 10) % 10]}"
            s, _ = Student.objects.get_or_create(student_id=sid, defaults={"full_name": name, "department": DEPTS[i % 5]})
            students.append(s)

        now = timezone.now()
        e, created = Election.objects.get_or_create(
            title="DIMT Student Union Elections",
            defaults=dict(description="Elect the student union executive committee.",
                          start_time=now - timedelta(hours=6), end_time=now + timedelta(days=2),
                          visibility=Election.LIVE))
        if not created:
            self.stdout.write("Demo election already exists.")
            return
        spec = {"Chairperson": ["Chikondi Banda", "Thoko Phiri", "Limbani Mwale"],
                "Secretary General": ["Tadala Chirwa", "Wongani Nyirenda"],
                "Treasurer": ["Dalitso Gondwe", "Yamikani Msiska", "Kondwani Kalua"]}
        for order, (title, names) in enumerate(spec.items()):
            p = Position.objects.create(election=e, title=title, order=order)
            for n in names:
                c = Candidate.objects.create(position=p, name=n, department=random.choice(DEPTS),
                                             manifesto="A stronger, more transparent union for every student.")
                for k, (t, d) in enumerate(PROGRAMS.get(n, [("Listen to every student", "Hold open forums every month.")])):
                    CandidateProgram.objects.create(candidate=c, title=t, details=d, order=k)

        for s in students[:36]:
            if random.random() < 0.6:
                choices = {}
                for p in e.positions.all():
                    cands = list(p.candidates.values_list("id", flat=True))
                    w = [5, 3, 2][:len(cands)]
                    choices[p.id] = [random.choices(cands, weights=w)[0]] if random.random() > 0.08 else []
                services.cast_ballot(s, e, choices)
        first = students[-1]
        self.stdout.write(self.style.SUCCESS(
            f"Demo ready. 60 students DIMT001..DIMT060. Try logging in as ID {first.student_id}, name '{first.full_name}' (this student has not voted)."))

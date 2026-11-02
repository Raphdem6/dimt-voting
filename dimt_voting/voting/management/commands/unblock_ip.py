from django.core.management.base import BaseCommand

from voting.models import BlockedIP, Student


class Command(BaseCommand):
    help = "Unblock a device (IP address) and optionally remove a student's blacklist. Use --all to clear every block."

    def add_arguments(self, parser):
        parser.add_argument("ip", nargs="?")
        parser.add_argument("--student", help="Student ID number to remove from the blacklist")
        parser.add_argument("--all", action="store_true")

    def handle(self, *args, **o):
        if o["all"]:
            n = BlockedIP.objects.all().delete()[0]
        elif o["ip"]:
            n = BlockedIP.objects.filter(ip=o["ip"]).delete()[0]
        else:
            n = 0
        self.stdout.write(f"Unblocked {n} device(s).")
        if o["student"]:
            c = Student.objects.filter(student_id=o["student"].upper()).update(is_blacklisted=False, blacklist_reason="")
            self.stdout.write(f"Un-blacklisted {c} student(s).")

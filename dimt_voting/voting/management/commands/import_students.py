from django.core.management.base import BaseCommand, CommandError

from voting import services


class Command(BaseCommand):
    help = "Import the student list from a CSV (columns: student_id, full_name, department, phone). Same as the admin upload."

    def add_arguments(self, parser):
        parser.add_argument("csv_file")
        parser.add_argument("--mark-missing", action="store_true", help="Mark students not in the file as not eligible")

    def handle(self, *args, **opts):
        try:
            with open(opts["csv_file"], "rb") as f:
                r = services.import_students(f, opts["mark_missing"])
        except OSError as e:
            raise CommandError(e)
        self.stdout.write(self.style.SUCCESS(f"Added {r['created']}, updated {r['updated']}, skipped {r['skipped']}."))
        for e in r["errors"]:
            self.stdout.write(self.style.WARNING(e))

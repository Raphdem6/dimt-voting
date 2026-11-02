"""Business logic: casting votes, audit log, and building the public results payload."""
import hashlib
from collections import OrderedDict

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.db.models.functions import TruncHour
from django.utils import timezone

from .models import AuditLog, Candidate, Election, Student, Vote, VoterRecord, normalize_id


class VoteError(Exception):
    pass


# ---------------------------------------------------------------- audit log
def log(action, actor="", detail=""):
    with transaction.atomic():
        last = AuditLog.objects.select_for_update().order_by("-id").first()
        prev = last.hash if last else ""
        entry = AuditLog(actor=actor, action=action, detail=detail[:300], prev_hash=prev)
        entry.save()
        payload = f"{entry.id}|{entry.created_at.isoformat()}|{actor}|{action}|{entry.detail}|{prev}"
        entry.hash = hashlib.sha256(payload.encode()).hexdigest()
        entry.save(update_fields=["hash"])
    return entry


def verify_audit_chain():
    prev = ""
    for e in AuditLog.objects.all():
        payload = f"{e.id}|{e.created_at.isoformat()}|{e.actor}|{e.action}|{e.detail}|{prev}"
        if e.prev_hash != prev or e.hash != hashlib.sha256(payload.encode()).hexdigest():
            return False, e.id
        prev = e.hash
    return True, None


# ---------------------------------------------------------------- voting
@transaction.atomic
def cast_ballot(student: Student, election: Election, choices: dict):
    """choices = {position_id: [candidate_id, ...]}  (empty list = abstain)
    Creates a VoterRecord (who) and anonymous Vote rows (what) in ONE transaction."""
    if not student.is_eligible or student.is_blacklisted:
        raise VoteError("You are not eligible to vote in this election.")
    if not election.is_open or not election.is_active:
        raise VoteError("This election is not open for voting.")

    try:
        with transaction.atomic():
            record = VoterRecord.objects.create(election=election, student=student)
    except IntegrityError:
        raise VoteError("You have already voted in this election.")

    hour = timezone.now().replace(minute=0, second=0, microsecond=0)
    for position in election.positions.all():
        picked = choices.get(position.id, [])
        valid_ids = set(position.candidates.filter(is_approved=True).values_list("id", flat=True))
        if len(picked) > position.max_choices:
            raise VoteError(f"Too many choices for {position.title} (max {position.max_choices}).")
        if len(set(picked)) != len(picked) or any(c not in valid_ids for c in picked):
            raise VoteError(f"Invalid selection for {position.title}.")
        if not picked:
            if not position.allow_abstain:
                raise VoteError(f"You must choose a candidate for {position.title}.")
            Vote.objects.create(position=position, candidate=None, cast_hour=hour)
        else:
            Vote.objects.bulk_create([Vote(position=position, candidate_id=c, cast_hour=hour) for c in picked])

    log("VOTE_CAST", actor="system", detail=f"Ballot recorded for election #{election.id}")  # no student id: secrecy
    return record


# ---------------------------------------------------------------- results
def turnout_stats(election):
    eligible = Student.objects.filter(is_eligible=True, is_blacklisted=False).count()
    voted = election.voter_records.count()
    pct = round(voted * 100 / eligible, 1) if eligible else 0
    by_branch = []
    depts = Student.objects.filter(is_eligible=True).values_list("department", flat=True).distinct()
    for b in sorted(set(depts)):
        total = Student.objects.filter(is_eligible=True, department=b).count()
        v = election.voter_records.filter(student__department=b).count()
        by_branch.append({"branch": b or "Unassigned", "eligible": total, "voted": v,
                          "percent": round(v * 100 / total, 1) if total else 0})
    return {"eligible": eligible, "voted": voted, "percent": pct, "by_branch": by_branch}


def results_visible(election):
    """Decide what the PUBLIC may see right now."""
    if election.hide_results_now:
        return "hidden"
    if election.has_closed:
        return "full"
    if election.status == "upcoming":
        return "hidden"
    return {"live": "full", "turnout": "turnout", "hidden": "hidden"}[election.visibility]


def public_results(election):
    mode = results_visible(election)
    data = {
        "election": {"id": election.id, "title": election.title, "status": election.status,
                     "start": election.start_time.isoformat(), "end": election.end_time.isoformat(),
                     "certified": election.is_certified,
                     "provisional": election.has_closed and not election.is_certified},
        "mode": mode,
        "updated": timezone.now().isoformat(),
        "turnout": None,
        "positions": [],
    }
    if mode == "hidden":
        return data
    data["turnout"] = turnout_stats(election)
    if mode == "turnout":
        return data

    for pos in election.positions.all():
        total_votes = pos.votes.filter(candidate__isnull=False).count()
        abstain = pos.votes.filter(candidate__isnull=True).count()
        p = {"id": pos.id, "title": pos.title, "total": total_votes, "abstain": abstain,
             "withheld": False, "candidates": [], "timeline": [], "margin": None, "leader": None}
        if total_votes + abstain < election.min_votes_to_show:
            p["withheld"] = True
            data["positions"].append(p)
            continue
        counts = dict(pos.votes.filter(candidate__isnull=False).values_list("candidate").annotate(n=Count("id")))
        rows = []
        for c in pos.candidates.filter(is_approved=True):
            n = counts.get(c.id, 0)
            rows.append({"id": c.id, "name": c.name, "branch": c.department, "votes": n,
                         "percent": round(n * 100 / total_votes, 1) if total_votes else 0,
                         "photo": c.photo.url if c.photo else ""})
        rows.sort(key=lambda r: (-r["votes"], r["name"]))
        p["candidates"] = rows
        if rows and rows[0]["votes"] > 0:
            p["leader"] = rows[0]["name"]
            if len(rows) > 1:
                p["margin"] = rows[0]["votes"] - rows[1]["votes"]
        # votes over time (hourly, cumulative)
        hourly = (pos.votes.annotate(h=TruncHour("cast_hour")).values("h").annotate(n=Count("id")).order_by("h"))
        running = 0
        for h in hourly:
            running += h["n"]
            p["timeline"].append({"t": h["h"].isoformat(), "total": running})
        data["positions"].append(p)
    return data


# ---------------------------------------------------------------- voter roll import (CSV uploaded by admin)
import csv
import io
import re as _re

ID_ALIASES = {"student_id", "id", "id_number", "idnumber", "id_no", "reg_no", "registration_number", "student_number"}
NAME_ALIASES = {"full_name", "fullname", "name", "student_name"}
DEPT_ALIASES = {"department", "dept", "class", "program", "programme", "course"}
PHONE_ALIASES = {"phone", "phone_number", "mobile", "tel"}
ID_PATTERN = _re.compile(r"^[A-Za-z0-9/_.\-]{2,40}$")
MAX_ROWS = 20000


def _header_key(h):
    return _re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


def import_students(file_obj, mark_missing_ineligible=False):
    """Create/update students from a CSV. Needs an ID column and a name column
    (full_name, or first_name + last_name). Returns a result dict."""
    raw = file_obj.read()
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raw = raw.decode("latin-1")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        return {"created": 0, "updated": 0, "skipped": 0, "errors": ["The file is empty."]}
    keys = {_header_key(h): h for h in reader.fieldnames}

    def col(aliases):
        return next((keys[k] for k in keys if k in aliases), None)

    c_id, c_name, c_dept, c_phone = col(ID_ALIASES), col(NAME_ALIASES), col(DEPT_ALIASES), col(PHONE_ALIASES)
    c_first, c_last = keys.get("first_name"), keys.get("last_name") or keys.get("surname")
    if not c_id or not (c_name or (c_first and c_last)):
        return {"created": 0, "updated": 0, "skipped": 0,
                "errors": ["Missing columns. The first row must contain an ID column (student_id) and a name column "
                           "(full_name, or first_name and last_name)."]}

    created = updated = skipped = 0
    errors, seen = [], set()
    with transaction.atomic():
        for n, row in enumerate(reader, start=2):
            if n - 1 > MAX_ROWS:
                errors.append(f"Stopped at {MAX_ROWS} rows (file too large).")
                break
            sid = normalize_id(row.get(c_id))
            name = " ".join((row.get(c_name) or "").split()) if c_name else \
                " ".join(f"{row.get(c_first) or ''} {row.get(c_last) or ''}".split())
            if not sid and not name:
                continue                                  # blank line
            if not ID_PATTERN.match(sid):
                skipped += 1
                errors.append(f"Row {n}: invalid ID number '{(row.get(c_id) or '')[:30]}'.")
                continue
            if len(name) < 2 or len(name) > 150:
                skipped += 1
                errors.append(f"Row {n}: missing or invalid name for ID {sid}.")
                continue
            if sid in seen:
                skipped += 1
                errors.append(f"Row {n}: ID {sid} appears twice in the file.")
                continue
            seen.add(sid)
            defaults = {"full_name": name, "department": ((row.get(c_dept) or "").strip()[:100] if c_dept else ""),
                        "phone": ((row.get(c_phone) or "").strip()[:30] if c_phone else "")}
            student = Student.objects.filter(student_id=sid).first()
            if student:
                student.full_name = name
                if c_dept:
                    student.department = defaults["department"]
                if c_phone:
                    student.phone = defaults["phone"]
                student.is_eligible = True
                student.save()
                updated += 1
            else:
                Student.objects.create(student_id=sid, **defaults)
                created += 1
        if mark_missing_ineligible and seen:
            Student.objects.exclude(student_id__in=seen).update(is_eligible=False)
    log("ROLL_IMPORTED", "admin", f"created={created} updated={updated} skipped={skipped}")
    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors[:25],
            "more_errors": max(0, len(errors) - 25)}

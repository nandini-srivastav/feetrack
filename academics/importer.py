"""Parse and validate a batch CSV of test scores or weekly attendance.

Validation happens before anything is saved; staff see every problem row
and can import only the valid ones. Re-uploading the same test or week
updates rows instead of duplicating them.
"""
import csv
import io
from decimal import Decimal, InvalidOperation

from django.db import transaction

from academics.models import TestScore, WeeklyAttendance
from fees.models import Student

COLUMNS = {"scores": ["student_id", "score"], "attendance": ["student_id", "classes_held", "classes_attended"]}
MAX_ROWS = 2000


def parse(raw: bytes, kind: str, batch, max_score=None):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [{"row": 0, "problem": "File is not UTF-8 text. Save it as CSV UTF-8 and try again."}]
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in COLUMNS[kind] if c not in headers]
    if missing:
        return [], [{"row": 1, "problem": f"Missing column(s): {', '.join(missing)}"}]
    in_batch = set(Student.objects.filter(batch=batch, status=Student.Status.ACTIVE).values_list("student_id", flat=True))

    ok, errors, seen = [], [], set()
    for n, raw_row in enumerate(reader, start=2):
        if n > MAX_ROWS + 1:
            errors.append({"row": n, "problem": f"Only the first {MAX_ROWS} rows are read."})
            break
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items()}
        sid = row.get("student_id", "").upper()
        problem = None
        if not sid:
            problem = "Student ID is empty"
        elif sid in seen:
            problem = "Student appears twice in this file"
        elif sid not in in_batch:
            problem = "Not an active student in this batch"
        seen.add(sid)
        parsed = {"row": n, "student_id": sid}
        if problem is None and kind == "scores":
            try:
                score = Decimal(row["score"])
                if score < 0 or (max_score is not None and score > max_score):
                    problem = f"Score must be between 0 and {max_score}"
                parsed["score"] = str(score)
            except (InvalidOperation, ValueError):
                problem = "Score is not a number"
        elif problem is None:
            try:
                held, attended = int(row["classes_held"]), int(row["classes_attended"])
                if held <= 0 or held > 60:
                    problem = "Classes held must be 1–60"
                elif not 0 <= attended <= held:
                    problem = "Classes attended must be between 0 and classes held"
                parsed.update(held=held, attended=attended)
            except ValueError:
                problem = "Class counts must be whole numbers"
        if problem:
            errors.append({**parsed, "problem": problem, "value": row.get("score") or row.get("classes_attended", "")})
        else:
            ok.append(parsed)
    return ok, errors


@transaction.atomic
def save(rows, kind, batch, *, test_name=None, test_date=None, max_score=None, week_start=None):
    students = {s.student_id: s for s in Student.objects.filter(batch=batch, student_id__in=[r["student_id"] for r in rows])}
    for r in rows:
        s = students[r["student_id"]]
        if kind == "scores":
            TestScore.objects.update_or_create(student=s, test_name=test_name, defaults={
                "test_date": test_date, "score": Decimal(r["score"]), "max_score": max_score})
        else:
            WeeklyAttendance.objects.update_or_create(student=s, week_start=week_start, defaults={
                "classes_held": r["held"], "classes_attended": r["attended"]})
    return len(rows)

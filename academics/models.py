from django.db import models


class WeeklyAttendance(models.Model):
    """Attendance per student per week (classes held vs attended).

    Weekly totals are enough for the parent view and the risk model; the
    centres' existing roll-call stays the source for individual classes.
    """

    student = models.ForeignKey("fees.Student", on_delete=models.CASCADE, related_name="attendance")
    week_start = models.DateField()
    classes_held = models.PositiveSmallIntegerField()
    classes_attended = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["week_start"]
        unique_together = [("student", "week_start")]
        constraints = [
            models.CheckConstraint(condition=models.Q(classes_attended__lte=models.F("classes_held")),
                                   name="attended_not_more_than_held"),
        ]

    @property
    def percent(self):
        return round(100 * self.classes_attended / self.classes_held) if self.classes_held else 0


class TestScore(models.Model):
    __test__ = False  # not a pytest class
    student = models.ForeignKey("fees.Student", on_delete=models.CASCADE, related_name="scores")
    test_name = models.CharField(max_length=80)
    test_date = models.DateField()
    score = models.DecimalField(max_digits=6, decimal_places=1)
    max_score = models.DecimalField(max_digits=6, decimal_places=1)

    class Meta:
        ordering = ["test_date"]
        unique_together = [("student", "test_name")]
        constraints = [
            models.CheckConstraint(condition=models.Q(score__gte=0) & models.Q(score__lte=models.F("max_score")),
                                   name="score_within_max"),
        ]

    @property
    def percent(self):
        return float(self.score) * 100 / float(self.max_score) if self.max_score else 0

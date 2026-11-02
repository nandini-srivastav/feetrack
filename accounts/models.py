from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """One user table for staff and parents; `role` decides what they see.

    Staff (accounts executive, counsellor) are scoped to one centre.
    The director sees every centre. Parents sign in by phone + OTP and
    only ever see their own children.
    """

    class Role(models.TextChoices):
        PARENT = "parent", "Parent"
        ACCOUNTS = "accounts", "Accounts executive"
        COUNSELLOR = "counsellor", "Counsellor"
        DIRECTOR = "director", "Director"

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.PARENT)
    centre = models.ForeignKey("fees.Centre", null=True, blank=True, on_delete=models.PROTECT, related_name="staff")
    phone = models.CharField(max_length=15, unique=True, null=True, blank=True)
    preferred_language = models.CharField(max_length=2, choices=[("hi", "हिन्दी"), ("en", "English")], default="hi")

    @property
    def is_staff_role(self):
        return self.role in {self.Role.ACCOUNTS, self.Role.COUNSELLOR, self.Role.DIRECTOR}

    @property
    def sees_all_centres(self):
        return self.role == self.Role.DIRECTOR or self.is_superuser

    def display_name(self):
        return self.get_full_name() or self.username


class OTPChallenge(models.Model):
    """A one-time code sent to a parent's phone. Only a hash is stored."""

    phone = models.CharField(max_length=15, db_index=True)
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)

    MAX_ATTEMPTS = 5

    class Meta:
        ordering = ["-created_at"]


class ConsentRecord(models.Model):
    """DPDP Act 2023: verifiable consent from the parent of a minor.

    Recorded the first time a parent signs in (after OTP proves they hold
    the registered phone). Stores which policy text they agreed to.
    """

    parent = models.ForeignKey(User, on_delete=models.CASCADE, related_name="consents")
    student = models.ForeignKey("fees.Student", on_delete=models.CASCADE, related_name="consents")
    policy_version = models.CharField(max_length=16)
    given_at = models.DateTimeField(auto_now_add=True)
    method = models.CharField(max_length=32, default="otp-verified-phone")

    class Meta:
        unique_together = [("parent", "student", "policy_version")]

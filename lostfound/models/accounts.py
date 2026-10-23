from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    class MemberType(models.TextChoices):
        STUDENT = "student", "Student"
        STAFF = "staff", "Staff"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    member_type = models.CharField(max_length=10, choices=MemberType.choices, default=MemberType.STUDENT)
    department = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} ({self.get_member_type_display()})"

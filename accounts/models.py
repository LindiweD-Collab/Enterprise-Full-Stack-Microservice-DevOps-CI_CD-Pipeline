from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Platform user. ``role`` drives API permissions (admin vs standard)."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        USER = "user", "Standard user"

    email = models.EmailField("email address", unique=True)
    role = models.CharField(
        max_length=10, choices=Role.choices, default=Role.USER, db_index=True
    )

    REQUIRED_FIELDS = ["email"]

    class Meta:
        ordering = ["id"]

    @property
    def is_admin_role(self) -> bool:
        return self.role == self.Role.ADMIN or self.is_superuser

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        if self.is_superuser:
            self.role = self.Role.ADMIN
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.username

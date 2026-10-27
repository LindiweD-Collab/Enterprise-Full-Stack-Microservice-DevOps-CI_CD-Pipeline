"""Create demo users, a venue with seats, and one published event.

Idempotent and intended for local development only: the credentials it
creates are public (see the README).
"""

import os
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from ticketing.models import Event, Seat, Venue

User = get_user_model()


class Command(BaseCommand):
    help = "Seed demo users, a venue with seats, and a published event (dev only)."

    def handle(self, *args, **options):
        admin_password = os.environ.get("SEED_ADMIN_PASSWORD", "Admin#Demo2026")
        user_password = os.environ.get("SEED_USER_PASSWORD", "User#Demo2026")

        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "email": "admin@halcyon.test",
                "role": User.Role.ADMIN,
                "is_staff": True,
            },
        )
        if created:
            admin.set_password(admin_password)
            admin.save()
        user, created = User.objects.get_or_create(
            username="alice", defaults={"email": "alice@halcyon.test"}
        )
        if created:
            user.set_password(user_password)
            user.save()

        venue, _ = Venue.objects.get_or_create(
            name="Halcyon Arena",
            city="Johannesburg",
            defaults={"address": "1 Rivonia Road, Sandton", "capacity": 60},
        )
        if not venue.seats.exists():
            Seat.objects.bulk_create(
                [
                    Seat(
                        venue=venue,
                        section="Floor" if row in "AB" else "Balcony",
                        row=row,
                        number=number,
                        price_multiplier=Decimal("2.00" if row in "AB" else "1.00"),
                    )
                    for row in "ABCDEF"
                    for number in range(1, 11)
                ]
            )

        if not Event.objects.filter(venue=venue, title="Launch Night").exists():
            start = timezone.now() + timedelta(days=30)
            Event.objects.create(
                venue=venue,
                title="Launch Night",
                description="An evening of live music at Halcyon Arena.",
                starts_at=start,
                ends_at=start + timedelta(hours=3),
                base_price=Decimal("350.00"),
                status=Event.Status.PUBLISHED,
                created_by=admin,
            )
        self.stdout.write(
            self.style.SUCCESS(
                "Demo data ready. admin / alice accounts exist "
                "(see README for demo passwords)."
            )
        )

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from board import notify
from board.models import Item


class Command(BaseCommand):
    help = "E-mail assignees about items due within a day. Safe to run as often as you like."

    def handle(self, *args, **options):
        today = timezone.localdate()
        due = Item.objects.filter(
            is_done=False, deadline__isnull=False, deadline__lte=today + timedelta(days=1)
        ).exclude(deadline_reminded_on=today)
        count = 0
        for item in due:
            notify.deadline(item)
            item.deadline_reminded_on = today
            item.save(update_fields=["deadline_reminded_on"])
            count += 1
        self.stdout.write(f"{count} reminder(s) sent")

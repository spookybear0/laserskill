from django.db import models
from django.utils import timezone

class Banner(models.Model):
    content = models.TextField()
    color = models.CharField(max_length=7, default="#FFFFFF", help_text="Hex color code for the banner background.")
    text_color = models.CharField(max_length=7, default="#000000", help_text="Hex color code for the banner text.")
    created_at = models.DateTimeField(auto_now_add=True)
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True, blank=True, help_text="When to stop showing this announcement.")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.content

    @property
    def is_visible(self):
        now = timezone.now()
        if self.end_date is None:
            return self.is_active and (self.start_date <= now)
        return self.is_active and (self.start_date <= now <= self.end_date)
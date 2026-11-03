from django.templatetags.static import static
from django.urls import reverse
from jinja2 import Environment
from django.db.models import Sum, Avg
from asgiref.sync import sync_to_async
from .models.banner import Banner
import os

if os.name == "nt":
    day_no_leading_zero = "%#d"
else:
    day_no_leading_zero = "%-d"


banners = Banner.objects.filter(is_active=True).order_by("-created_at")

def environment(**options):
    """Configures the Jinja2 environment with Django global functions."""
    env = Environment(**options)

    from .models.types import SITES, SITE_BY_ID, COMPETITIVE_SITES, SITE_BY_IPL_NAME, IntRole, EntityType, RoleLock, NAME_TO_TEAM

    # inject Django helper functions into Jinja2 templates
    env.globals.update(
        {
            "url": reverse,
            "static": static,
            "zip": zip,
            "day_no_leading_zero": day_no_leading_zero,
            "SITES": SITES,
            "SITE_BY_ID": SITE_BY_ID,
            "COMPETITIVE_SITES": COMPETITIVE_SITES,
            "SITE_BY_IPL_NAME": SITE_BY_IPL_NAME,
            "NAME_TO_TEAM": NAME_TO_TEAM,
            "IntRole": IntRole,
            "EntityType": EntityType,
            "RoleLock": RoleLock,
            "Sum": Sum,
            "Avg": Avg,
            "sync_to_async": sync_to_async,
            "banners": banners
        }
    )

    return env
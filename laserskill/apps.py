
from django.core.cache import cache
from django.apps import AppConfig
from asgiref.sync import async_to_sync
from .config import config
import os
import sys


async def startup_async():
    from laserskill.tdf import download_legacy_tdf, download_tdfs, mass_parse_tdfs
    #await download_tdfs(site_id="4-19", game_type="sm5")
    #await mass_parse_tdfs()

def startup():
    # not debug
    if not config.get("debug", True):
        from laserskill.rating import calculate_predictive_accuracy_task
        calculate_predictive_accuracy_task()

        


class LaserSkillConfig(AppConfig):
    name = "laserskill"

    def ready(self):
        if os.environ.get("RUN_MAIN") == "true" or not os.environ.get("RUN_MAIN") and "runserver" in sys.argv:
            startup()
            async_to_sync(startup_async)()
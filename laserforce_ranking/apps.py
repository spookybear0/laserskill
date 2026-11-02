
from django.core.cache import cache
from django.apps import AppConfig
from asgiref.sync import async_to_sync
import os
import sys


async def startup_async():
    from laserforce_ranking.tdf import download_legacy_tdf, download_tdfs, mass_parse_tdfs
    #await download_tdfs(site_id="4-19", game_type="sm5")
    #await mass_parse_tdfs()

def startup():
    from laserforce_ranking.rating import calculate_predictive_accuracy
    #calculate_predictive_accuracy()


class LaserforceRankingConfig(AppConfig):
    name = "laserforce_ranking"

    def ready(self):
        if os.environ.get("RUN_MAIN") == "true" or not os.environ.get("RUN_MAIN") and "runserver" in sys.argv:
            startup()
            async_to_sync(startup_async)()
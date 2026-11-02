from django.views import View
from django.shortcuts import render
from laserforce_ranking.models import Player, Game
from laserforce_ranking.rating import calculate_predictive_accuracy
from django.core.cache import cache

class IndexView(View):
    def get(self, request):
        """
        Handle GET requests for the index page.
        """

        predictive_accuracy = cache.get("predictive_accuracy")

        calculate_predictive_accuracy()
        
        return render(request, "index.html", context={
            "total_players": Player.objects.count(),
            "total_ranked_games": Game.objects.filter(ranked=True).count(),
            "predictive_accuracy": predictive_accuracy,
        })
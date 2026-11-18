from script import setup_environment
setup_environment()
import click

from laserskill.rating import update_sm5_rankings
from laserskill.models import SM5Game

@click.command()
@click.option("--game-id", type=int, required=True, help="The ID of the game to rate.")
def rate_game_command(game_id: int):
    """
    Command to run ratings for a single (sm5) TDF, after it's been imported
    """
    game = SM5Game.objects.get(id=game_id)

    if not game:
        click.echo(f"Game with ID {game_id} does not exist. Exiting.")
        return
    
    click.echo(f"Rating game: {game}")

    update_sm5_rankings(game)
    

if __name__ == "__main__":
    rate_game_command()
from script import setup_environment
setup_environment()
import click
from laserskill.models import Game
from laserskill.rating import recalculate_rankings
import time



@click.command()
@click.option("--start-game-id", type=int, default=None, help="The game ID to start recalculation after.")
def recalculate_ratings_command(start_game_id: int = None):
    """
    Command to recalculate ratings after mass parsing TDFs.
    """
    click.echo("Starting recalculation of ratings...")

    if start_game_id is not None:
        click.echo(f"Recalculating ratings starting from game ID: {start_game_id}")
        game = Game.objects.filter(id=start_game_id).first()
        if not game:
            click.echo(f"Game with ID {start_game_id} does not exist. Exiting.")
            return
        print(f"Starting recalculation from game: {game}, make sure this is the correct game to start after.")
        time.sleep(5)

    start_time = time.time()
    recalculate_rankings(start_game_id=start_game_id)
    click.echo("Recalculation of ratings completed in {:.2f} seconds.".format(time.time() - start_time))



if __name__ == "__main__":
    recalculate_ratings_command()
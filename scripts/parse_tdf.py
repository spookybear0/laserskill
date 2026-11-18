from script import setup_environment
setup_environment()
import click

from laserskill.tdf import parse_tdf
from pathlib import Path

@click.command()
@click.option("--file-location", type=Path, required=True, help="The location of the TDF file to parse.")
@click.option("--skip-rankings", is_flag=True, default=False, help="Skip recalculating rankings after parsing.")
def parse_tdf_command(file_location: Path, skip_rankings: bool):
    """
    Command to parse a single TDF.
    """

    parse_tdf(file_location, skip_rankings=skip_rankings)

if __name__ == "__main__":
    parse_tdf_command()
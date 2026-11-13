from script import setup_environment
setup_environment()
import asyncclick as click

from laserskill.tdf import mass_parse_tdfs
import time

@click.command()
@click.option('--skip-rankings', is_flag=True, default=True, help='Skip ranking after parsing TDFs')
@click.option('--dont-skip', is_flag=True, default=False, help='Bypass only looking for tdfs after most recently saved')
async def mass_parse_tdfs_command(skip_rankings: bool = True, dont_skip: bool = False):
    """
    Command to mass parse TDFs.
    """
    click.echo("Starting mass parsing of TDFs...")
    start_time = time.time()
    await mass_parse_tdfs(skip_rankings=skip_rankings, dont_skip=dont_skip)
    click.echo(f"Mass parsing of TDFs completed in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    mass_parse_tdfs_command(_anyio_backend="asyncio")
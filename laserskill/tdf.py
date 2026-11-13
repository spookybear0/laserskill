from pathlib import Path
from laserskill.models.types import EntityType, EventType, EntityEndType, PlayerStateType, IntRole, SITE_BY_ID, TeamType
from laserskill.models.game import Game, Team, EntityStart, Event, EntityEnd, PlayerState, ScoreEvent, StepEvent, ShotEvent, LifeEvent, MissileEvent, ChangeTeamEvent
from laserskill.models.sm5 import SM5Stats, SM5Game
from laserskill.models.laserball import LaserballStats
from typing import Optional, List, Dict
from .rating import SM5_RANK_VERSION, update_sm5_rankings, MU, SIGMA, BLANK_RATING_PER_SITE, BLANK_ENTITY_RATING
from laserskill.models.player import Player
import aiohttp
from django.db import transaction
from asgiref.sync import async_to_sync, sync_to_async
import os
import traceback
from datetime import timedelta, datetime
from django.utils import timezone
import logging
import asyncio
from concurrent.futures import ProcessPoolExecutor

logger = logging.getLogger(__name__)

def element_to_color(element: str) -> str:
    conversion = {
        "Fire": "Red",
        "Ice": "Blue",
        "Earth": "Green",
        "None": "None",

        # extras for edge cases
        "Green": "Green",
        "Red": "Red",
        "Blue": "Blue",
        "Yellow": "Yellow",
        "Purple": "Purple",
        "Solid Green": "Green",
        "Solid Red": "Red",
        "Solid Blue": "Blue",
        "Solid Yellow": "Yellow",
        "Solid Purple": "Purple",
    }

    return conversion[element]

# tdf importing/uploading

async def download_legacy_tdf():
    """
    Imports tdfs from the old laserforce_ranking website

    Both sm5 and laserball
    """
    
    # https://laserforce.spoo.uk/api/game/<type>/<id>

    break_amount = 5 # how many 404s in a row to break the loop, since there are gaps in the ids
    current_breaks = 0
    for game_type in ["sm5", "laserball"]:
        i = 1
        while True:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://laserforce.spoo.uk/api/game/{game_type}/{i}/tdf") as resp:
                    if resp.status == 404 or "404 - Not Found" in (await resp.text()):
                        current_breaks += 1
                        if current_breaks >= break_amount:
                            break
                        else:
                            i += 1
                            continue
                    
                    data = await resp.json()

                    # save tdf to disk
                    tdf_name = f"sm5_{data['id']}.tdf"
                    tdf_path = Path(f"tdfs/{tdf_name}")

                    with open(tdf_path, "w", encoding="utf-16") as f:
                        f.write(data["tdf"])

                    # process tdf
            i += 1


async def download_tdfs(start_date: Optional[str] = None, game_type: Optional[str] = None, site_id: Optional[str] = None):
    async with aiohttp.ClientSession() as session:
        payload = {
            "start_date": "1970-01-01"
        }
        if start_date:
            payload["end_date"] = start_date
        if site_id:
            payload["center"] = site_id
        if game_type:
            payload["game_type"] = game_type

        async with session.get("https://lfstats.com/api/games", params=payload) as response:
            games = (await response.json())["data"]

            for i, game in enumerate(reversed(games)):
                if game["competition_slug"]:
                    # competitive game, skip
                    continue

                # create /tdfs directory if it doesn't exist

                if not os.path.exists("tdfs"):
                    os.makedirs("tdfs")
                
                # download game into tdfs/ from game["tdf_url"]

                tdf_url = game["tdf_url"]
                tdf_filename = tdf_url.split("/")[-1]

                # check if we have the tdf already

                if os.path.exists(f"tdfs/{tdf_filename}"):
                    logger.info(f"Already have {tdf_filename}, skipping download")
                    continue

                async with session.get(tdf_url) as tdf_response:
                    if tdf_response.status == 200:
                        with open(f"tdfs/{tdf_filename}", "wb") as tdf_file:
                            logger.info(f"Downloading {tdf_filename} ({i+1}/{len(games)})")
                            tdf_file.write(await tdf_response.read())

    
async def mass_parse_tdfs(skip_rankings: bool = False):
    tdf_dir = Path("tdfs")
    files = list(tdf_dir.glob("*.tdf"))

    def key_func(x):
        date = x.stem.split("-")[-1].replace(".tdf", "") # get the last part of the filename, which is the date
        # convert to a datetime which is comparable

        return datetime.strptime(date, "%Y%m%d%H%M%S")

    files.sort(key=key_func) # sort by date in filename

    if skip_rankings:
        loop = asyncio.get_event_loop()
        with ProcessPoolExecutor() as executor:
            tasks = [
                loop.run_in_executor(executor, parse_tdf, tdf_file, skip_rankings)
                for tdf_file in files
            ]
            try:
                await asyncio.gather(*tasks)
            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt received, cancelling tasks...")
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info("All tasks cancelled.")
    else:
        for tdf_file in files:
            await sync_to_async(parse_tdf)(tdf_file, skip_rankings)

# tdf parsing

def parse_tdf(file_location: Path, skip_rankings: bool = False):
    # first we will process the general information about the game, then we will
    # process the more specific game type
    # current types allowed: sm5, laserball

    logger.info(f"Parsing tdf file: {file_location.name}")

    # check if file by this name is in the database, if so, skip it

    if Game.objects.filter(tdf_name=file_location.name).exists():
        logger.info(f"TDF file {file_location.name} already exists in the database, skipping")
        return

    # read line by line

    teams = {} # index: Team
    entity_starts = {} # entity_id: EntityStart
    events = []
    scores = []
    entity_ends = []
    sm5_stats = []
    change_team_events = []
    player_states = []
    step_events = []
    shot_events = []
    life_events = []
    missile_events = []

    force_ended_early = True # if the game ended early, this will be set to false if the game ended naturally
    end_event = None

    file = open(file_location, "r", encoding="utf-16")

    lines = file.readlines()

    # check if it's empty/really short
    if len(lines) < 10:
        logger.warning(f"TDF file {file_location.name} is empty or too short, skipping")
        return

    for line in lines:
        if not line:
            break

        data = line.rstrip("\n").split("\t")

        # example of a line: 0	2.006	8.704	4-23
        match data[0]:
            case ";":
                # this is a comment, ignore it
                continue
            case "0": # general info
                file_version = data[1]
                program_version = data[2]
                site = data[3] # site id, ex: 4-43
            case "1": # mission info
                mission_type = int(data[1]) # site dependant enum
                mission_name = data[2] # mission name in system, dependant on site
                start_time = data[3] # ex: 20260719204954 -> 2026-07-19 20:49:54 +07:00
                mission_duration = timedelta(milliseconds=int(data[4]) if len(data) > 4 else 900000) # how long the game can last if it doesn't end early, in milliseconds
                penalty_amount = int(data[5] if len(data) > 5 else -1000) # score added for each penalty (ex: 0, -1000)

                # TODO: Fix timezones so they show correctly on others' computers
                # tdf file has local timezone, and we have the site timezone. database converts into utc and saves

                # convert to datetime with timezone
                start_time_formatted = datetime.strptime(start_time, "%Y%m%d%H%M%S")
                start_time_formatted = timezone.make_aware(start_time_formatted, timezone=SITE_BY_ID[site].timezone)

                # check if we already have this game in the database, if so, skip it
                if Game.objects.filter(site_id=site, start_time=start_time_formatted).exists():
                    logger.info(f"Game {file_location.name} already exists in the database, skipping")
                    return
            case "2": # team info
                index = int(data[1])
                name = data[2]
                color_enum = int(data[3])
                color_name = data[4]
                real_color_name = element_to_color(color_name)

                teams[index] = Team(
                    index=index,
                    name=name,
                    color_enum=color_enum,
                    color_name=color_name,
                    real_color_name=real_color_name,
                    is_neutral=(color_enum == 0 or "Neutral" in name or "None" in color_name)
                )
            case "3": # entity start
                time = int(data[1]) # ms since start
                entity_id = data[2] # ex: #ZRbsz (member) or @71 (battlesuit/base)
                entity_type = EntityType(data[3])
                name = data[4]
                team_index = int(data[5])
                level = int(data[6])
                role = int(data[7]) # "category"
                battlesuit = data[8] if len(data) > 8 else None
                member_id = data[9] if len(data) > 9 else None

                team = teams.get(team_index)

                entity_starts[entity_id] = EntityStart(
                    time=time,
                    entity_id=entity_id,
                    type=entity_type,
                    name=name,
                    team=team,
                    level=level,
                    role=role,
                    battlesuit=battlesuit,
                    member_id=member_id
                )
            case "4": # event
                time = int(data[1])
                event_type = EventType(data[2])
                entity1 = data[3] if len(data) > 3 else ""
                action = data[4] if len(data) > 4 else ""
                entity2 = data[5] if len(data) > 5 else ""

                event = Event(
                    time=time,
                    type=event_type,
                    entity1=entity1,
                    action=action,
                    entity2=entity2
                )

                events.append(event) 

                if event_type == EventType.MISSION_END:  # game ended naturally\
                    end_event = event
                    force_ended_early = False
            case "5": # score deltas
                time = int(data[1])
                entity_id = data[2]
                old = int(data[3])
                delta = int(data[4])
                new = int(data[5])

                entity_start = entity_starts.get(entity_id)

                scores.append(ScoreEvent(
                    time=time,
                    entity=entity_start,
                    old=old,
                    delta=delta,
                    new=new
                ))
            case "6": # entity end
                time = int(data[1])
                entity_id = data[2]
                entity_end_type = EntityEndType(int(data[3]))
                score = int(data[4])

                entity_start = entity_starts.get(entity_id)

                entity_end = EntityEnd(
                    time=time,
                    entity=entity_start,
                    type=entity_end_type,
                    score=score
                )

                entity_ends.append(entity_end)
            case "7": # sm5 stats
                entity_id = data[1]
                shots_hit = int(data[2])
                shots_fired = int(data[3])
                times_zapped = int(data[4])
                times_missiled = int(data[5])
                missile_hits = int(data[6])
                nukes_detonated = int(data[7])
                nukes_activated = int(data[8])
                nuke_cancels = int(data[9])
                medic_hits = int(data[10])
                own_medic_hits = int(data[11])
                medic_nukes = int(data[12])
                scout_rapid_fires = int(data[13])
                life_boosts = int(data[14])
                ammo_boosts = int(data[15])
                lives_left = int(data[16])
                shots_left = int(data[17])
                penalties = int(data[18])
                shot_3_hits = int(data[19])
                own_nuke_cancels = int(data[20])
                shot_opponent = int(data[21])
                shot_team = int(data[22])
                missiled_opponent = int(data[23])
                missiled_team = int(data[24])

                entity_start = entity_starts.get(entity_id)
                entity_end = entity_start.entity_end

                sm5_stats.append(SM5Stats(
                    entity=entity_start,
                    entity_end=entity_end,
                    shots_hit=shots_hit,
                    shots_fired=shots_fired,
                    times_zapped=times_zapped,
                    times_missiled=times_missiled,
                    missile_hits=missile_hits,
                    nukes_detonated=nukes_detonated,
                    nukes_activated=nukes_activated,
                    nuke_cancels=nuke_cancels,
                    medic_hits=medic_hits,
                    own_medic_hits=own_medic_hits,
                    medic_nukes=medic_nukes,
                    scout_rapid_fires=scout_rapid_fires,
                    life_boosts=life_boosts,
                    ammo_boosts=ammo_boosts,
                    lives_left=lives_left,
                    shots_left=shots_left,
                    penalties=penalties,
                    shot_3_hits=shot_3_hits,
                    own_nuke_cancels=own_nuke_cancels,
                    shot_opponent=shot_opponent,
                    shot_team=shot_team,
                    missiled_opponent=missiled_opponent,
                    missiled_team=missiled_team
                ))
            case "8": # team change
                time = int(data[1])
                entity_id = data[2]
                old_team_index = int(data[3])
                new_team_index = int(data[4])

                old_team = teams.get(old_team_index)
                new_team = teams.get(new_team_index)

                entity_start = entity_starts.get(entity_id)

                change_team_events.append(ChangeTeamEvent(
                    time=time,
                    entity=entity_start,
                    old_team=old_team,
                    new_team=new_team
                ))
            case "9": # player state
                time = int(data[1])
                entity_id = data[2]
                player_state_type = PlayerStateType(int(data[3]))

                entity_start = entity_starts.get(entity_id)
        
                player_states.append(PlayerState(
                    time=time,
                    entity=entity_start,
                    state=player_state_type
                ))
            case "10": # step count
                time = int(data[1])
                entity_id = data[2]
                delta = int(data[3])
                step_count = int(data[4])

                entity_start = entity_starts.get(entity_id)

                step_events.append(StepEvent(
                    time=time,
                    entity=entity_start,
                    delta=delta,
                    new=step_count
                ))
            case "11": # life deltas
                time = int(data[1])
                entity_id = data[2]
                delta = int(data[3])
                lives = int(data[4])

                entity_start = entity_starts.get(entity_id)
                life_events.append(LifeEvent(
                    time=time,
                    entity=entity_start,
                    delta=delta,
                    new=lives
                ))
            case "12": # shot deltas
                time = int(data[1])
                entity_id = data[2]
                delta = int(data[3])
                shots = int(data[4])

                entity_start = entity_starts.get(entity_id)
                shot_events.append(ShotEvent(
                    time=time,
                    entity=entity_start,
                    delta=delta,
                    new=shots
                ))
            case "13": # missile deltas
                time = int(data[1])
                entity_id = data[2]
                delta = int(data[3])
                missiles = int(data[4])

                entity_start = entity_starts.get(entity_id)
                missile_events.append(MissileEvent(
                    time=time,
                    entity=entity_start,
                    delta=delta,
                    new=missiles
                ))
    with transaction.atomic():
                    
        # combine entity_ends.score onto team.score

        for entity_end in entity_ends:
            if entity_end.entity.team:
                if entity_end.entity.team.score is None:
                    entity_end.entity.team.score = 0
                entity_end.entity.team.score += entity_end.score

        # for any teams with no score, set their score to 0

        for team in teams.values():
            if team.score is None:
                team.score = 0

        # separate into team1 and team2
        # there is no specification which color team1 or team2 is

        active_teams = [
            t for t in teams.values()
            if t.color_name and t.color_enum and t.name != "Neutral"
        ]

        if len(active_teams) != 2:
            logger.warning("Game {file_location.name} does not have exactly 2 active teams, skipping")
            return

        team1, team2 = active_teams

        # get team sizes

        team1_size = len([e for e in entity_starts.values() if e.team == team1 and e.type == EntityType.PLAYER])
        team2_size = len([e for e in entity_starts.values() if e.team == team2 and e.type == EntityType.PLAYER])

        # get actual mission duration

        if end_event:
            duration = timedelta(milliseconds=end_event.time)
        elif events:
            last_event = events[-1]
            duration = timedelta(milliseconds=last_event.time)
        else:
            logger.warning(f"Game {file_location.name} has no events, skipping")
            return

        # we still want games if ended early and it was decently long, don't rank
        if force_ended_early and duration < timedelta(minutes=5):
            logger.info(f"Game {file_location.name} ended early, skipping")
        
        # players

        for entity_id, e in entity_starts.items():
            # is a player and logged in
            if entity_id.startswith("@") and (e.name == e.battlesuit or e.battlesuit is None or e.battlesuit == ""):
                continue

            if e.type == EntityType.PLAYER:
                # update player name if we have a new one
                if (player := Player.objects.filter(entity_id=entity_id).first()) and player.codename != e.name:
                    # add previous codename to previous_codenames if it doesn't already exist

                    if player.codename and player.codename not in player.previous_codenames:
                        player.previous_codenames.append(player.codename)

                    # if the new codename is in previous_codenames, remove it
                    if e.name in player.previous_codenames:
                        player.previous_codenames.remove(e.name)

                    player.codename = e.name
                    player.player_id = e.member_id 

                    player.save()
                # give player their player_id if we don't have it yet
                if (player := Player.objects.filter(entity_id=entity_id, player_id__isnull=True).first()) and e.member_id:
                    player.player_id = e.member_id
                    split_ = e.member_id.split("-")
                    player.home_site_id = f"{split_[0]}-{split_[1]}" if len(split_) > 1 else site

                    player.save()
                # create new player if we don't have their entity_id
                if not Player.objects.filter(entity_id=entity_id).exists():
                    if e.member_id:
                        split_ = e.member_id.split("-")
                        home_site_id = f"{split_[0]}-{split_[1]}" if len(split_) > 1 else site
                    else:
                        # for now set home site to this game site, but we will update it later when we get their player_id from the database
                        home_site_id = site

                    ratings = {
                        "global": BLANK_RATING_PER_SITE,
                    }
                    ratings[site] = BLANK_RATING_PER_SITE

                    Player.objects.create(player_id=e.member_id, codename=e.name, entity_id=entity_id, home_site_id=home_site_id, ratings=ratings)
        
        # before we save this game, change the file name into the correct format
        # ex: 4-80-20260830003930.tdf

        file.close()

        new_tdf_name = f"{site}-{start_time}.tdf"

        # find game type

        base_args = {
            "file_location": file_location,
            "site_id": site,
            "tdf_name": new_tdf_name,
            "file_version": file_version,
            "software_version": program_version,
            "mission_type": mission_type,
            "mission_name": mission_name,
            "force_ended_early": force_ended_early,
            "start_time": start_time_formatted,
            "mission_duration": mission_duration,
            "duration": duration,
            "penalty_amount": penalty_amount,
            "teams": teams,
            "entity_starts": entity_starts,
            "events": events,
            "scores": scores,
            "entity_ends": entity_ends,
            "change_team_events": change_team_events,
            "player_states": player_states,
            "step_events": step_events,
            "shot_events": shot_events,
            "life_events": life_events,
            "missile_events": missile_events,
            "team1": team1,
            "team2": team2,
            "team1_size": team1_size,
            "team2_size": team2_size,
            "skip_rankings": skip_rankings
        }
    
        if mission_type == 5: # sm5
            process_sm5(
                **base_args,
                sm5_stats=sm5_stats,
            )
        elif mission_type == 28: # laserball
            process_laserball(
                **base_args
            )
        else:
            logger.warning(f"Unknown mission type: {mission_type} ({mission_name})")

        # move the file to the new name if it is different

        if new_tdf_name != file_location.name:
            # move the file to the new name
            new_file_location = file_location.parent / new_tdf_name

            if not new_file_location.exists():
                file_location.rename(new_file_location)
                logger.info(f"Renamed {file_location.name} to {new_tdf_name}")
            else:
                logger.warning(f"File {new_tdf_name} already exists")
                return
            
        logger.info(f"Finished parsing tdf file: {file_location.name}")


def process_sm5(
    file_location: Path,
    site_id: str,
    tdf_name: str,
    file_version: str,
    software_version: str,
    mission_type: int,
    mission_name: str,
    force_ended_early: bool,
    start_time: str,
    mission_duration: timedelta,
    duration: timedelta,
    penalty_amount: int,
    teams: Dict[int, Team],
    entity_starts: Dict["str", EntityStart],
    events: List[Event],
    scores: List[ScoreEvent],
    entity_ends: List[EntityEnd],
    change_team_events: List[ChangeTeamEvent],
    player_states: List[PlayerState],
    step_events: List[StepEvent],
    shot_events: List[ShotEvent],
    life_events: List[LifeEvent],
    missile_events: List[MissileEvent],
    team1: Team,
    team2: Team,
    team1_size: int,
    team2_size: int,
    sm5_stats: List[SM5Stats],
    skip_rankings: bool = False
):
    try:
        # special points

        # key: player entity id, value: special points
        # this is needed because tdf doesn't save the ending special points
        # and leaderboards usually have this info
        # we'll update this dict as we parse events and then save the final values to the database when we parse the entity end events
        player_special_points: Dict[str, int] = {}
        # key: player entity id, value: whether the player can gain specials (False if heavy or has rapid fire on)
        player_can_gain_specials: Dict[str, bool] = {}

        for entity_id, entity_start in entity_starts.items():
            if entity_start.type == EntityType.PLAYER:
                # if this is a player, determine if they can gain specials (heavies can't gain specials and scouts can't gain specials until they get rapid fire)

                player_special_points[entity_id] = 0
                if entity_start.role == IntRole.HEAVY:
                    player_can_gain_specials[entity_id] = False
                else:
                    player_can_gain_specials[entity_id] = True

        for event in events:
            # handle special points

            if event.entity1 not in entity_starts or event.entity2 not in entity_starts or entity_starts[event.entity1].type != EntityType.PLAYER:
                continue

            if player_can_gain_specials.get(event.entity1, True): # if player can gain specials (not heavy or doesn't have rapid fire on)
                match event.type:
                    # give specials
                    case EventType.DAMAGED_OPPONENT | EventType.DOWNED_OPPONENT:
                        # only enemies (damaged/downed opponent still is used for teammates)
                        if entity_starts[event.entity1].team.name != entity_starts[event.entity2].team.name:
                            player_special_points[event.entity1] = min(player_special_points.get(event.entity1, 0) + 1, 99)
                    case EventType.MISSILE_DOWN_OPPONENT:
                        if entity_starts[event.entity1].team.name != entity_starts[event.entity2].team.name:
                            player_special_points[event.entity1] = min(player_special_points.get(event.entity1, 0) + 2, 99)

                    # remove specials
                    case EventType.ACTIVATE_NUKE:
                        player_special_points[event.entity1] = min(player_special_points.get(event.entity1, 0) - 20, 99)
                    case EventType.AMMO_BOOST:
                        player_special_points[event.entity1] = min(player_special_points.get(event.entity1, 0) - 15, 99)
                    case EventType.LIFE_BOOST:
                        player_special_points[event.entity1] = min(player_special_points.get(event.entity1, 0) - 10, 99)

            # scouts can get sp from bases even with rapid
            if event.type in [EventType.DESTROY_BASE, EventType.MISSILE_BASE_DESTROY, EventType.BASE_AWARDED] and entity_starts[event.entity1].role != IntRole.HEAVY:
                player_special_points[event.entity1] = min(player_special_points.get(event.entity1, 0) + 5, 99)

            # scout activated rapid
            if event.type == EventType.ACTIVATE_RAPID_FIRE:
                # rapid fire turned on, specials can't be gained until it's turned off
                player_special_points[event.entity1] = min(player_special_points.get(event.entity1, 0) - 10, 99)
                player_can_gain_specials[event.entity1] = False

            # scout deactivated rapid from being resupplied ammo or lives
            if event.type in [EventType.RESUPPLY_AMMO, EventType.RESUPPLY_LIVES]:
                if entity_starts[event.entity2].role == IntRole.SCOUT:
                    # rapid fire turned off, specials can be gained again
                    player_can_gain_specials[event.entity2] = True
        
        # create sm5 game

        game = SM5Game(
            site_id=site_id,
            tdf_name=tdf_name,
            file_version=file_version,
            software_version=software_version,
            mission_type=mission_type,
            mission_name=mission_name,
            ranked=False, # will be updated later
            force_ended_early=force_ended_early,
            start_time=start_time,
            mission_duration=mission_duration,
            duration=duration,
            penalty_amount=penalty_amount,
            last_team_standing=None,
            team1_size=team1_size,
            team2_size=team2_size
        )

        game.save()

        # save teams

        team_list = list(teams.values())

        for team in team_list:
            team.game = game

        Team.objects.bulk_create(team_list, batch_size=1000)

        # save entity starts

        entity_list = list(entity_starts.values())

        for entity in entity_list:
            entity.game = game

        EntityStart.objects.bulk_create(
            entity_list,
            batch_size=1000,
        )

        # save entity starts to teams
        entities_by_team = {}

        for entity in entity_list:
            entities_by_team.setdefault(entity.team, []).append(entity)

        for team, team_entities in entities_by_team.items():
            team.entity_starts.add(*team_entities)

        # save all other objects/events

        for event in events:
            event.game = game

        Event.objects.bulk_create(events, batch_size=1000)

        for score in scores:
            score.game = game

        ScoreEvent.objects.bulk_create(scores, batch_size=1000)

        for event in change_team_events:
            event.game = game

        ChangeTeamEvent.objects.bulk_create(
            change_team_events,
            batch_size=1000,
        )

        for event in player_states:
            event.game = game

        PlayerState.objects.bulk_create(
            player_states,
            batch_size=1000,
        )

        for event in step_events:
            event.game = game

        StepEvent.objects.bulk_create(step_events, batch_size=1000)

        for event in shot_events:
            event.game = game

        ShotEvent.objects.bulk_create(shot_events, batch_size=1000)

        for event in life_events:
            event.game = game

        LifeEvent.objects.bulk_create(life_events, batch_size=1000)

        for event in missile_events:
            event.game = game

        MissileEvent.objects.bulk_create(
            missile_events,
            batch_size=1000,
        )

        # save entity ends

        for entity_end in entity_ends:
            entity_end.game = game
            entity_end.entity.game = game

        EntityEnd.objects.bulk_create(
            entity_ends,
            batch_size=1000,
        )

        # relationship between entity starts and entity ends
        for entity_end in entity_ends:
            entity_end.entity.entity_end = entity_end

        EntityStart.objects.bulk_update(
            entity_list,
            ["entity_end"],
            batch_size=1000,
        )

        # save sm5 stats

        for stat in sm5_stats:
            entity_id = stat.entity.entity_id
            entity = entity_starts[entity_id]

            stat.special_points = player_special_points.get(entity_id, 0)
            stat.entity = entity
            stat.entity_end = entity.entity_end
            stat.game = game

        # create all sm5stats records
        SM5Stats.objects.bulk_create(
            sm5_stats,
            batch_size=1000,
        )

        # reverse reference
        for stat in sm5_stats:
            entity = stat.entity
            entity.sm5_stats_id = stat.pk

        # save all entity start -> sm5 stats relationships
        EntityStart.objects.bulk_update(
            [stat.entity for stat in sm5_stats],
            ["sm5_stats"],
            batch_size=1000,
        )

        # determine if the game should be ranked automatically

        ranked = True

        if force_ended_early:
            ranked = False

        # 5 < team size < 7 and teams are not of unequal size (ratings are not tested for unequal team sizes)

        if team1_size > 7 or team2_size > 7 or team1_size < 5 or team2_size < 5 or team1_size != team2_size:
            ranked = False

        # also check that our roles are correct
        # ex: a standard sm5 team has 1 commander, 1 heavy, 1 scout, 1 medic, 1 ammo, and 1-3 scouts

        # for each team
        for t in teams.values():
            total_count = 0
            commander_count = 0
            heavy_count = 0
            scout_count = 0
            ammo_count = 0
            medic_count = 0

            for e in entity_starts.values():
                if e.type == EntityType.PLAYER and e.team == t:
                    total_count += 1
                    if e.role == IntRole.COMMANDER:
                        commander_count += 1
                    elif e.role == IntRole.HEAVY:
                        heavy_count += 1
                    elif e.role == IntRole.SCOUT:
                        scout_count += 1
                    elif e.role == IntRole.AMMO: # sometimes we have 2 ammos, but for ranking purposes we only want games with 1
                        ammo_count += 1
                    elif e.role == IntRole.MEDIC:
                        medic_count += 1

            if total_count == 0:  # probably a neutral team
                continue

            if commander_count != 1 or heavy_count != 1 or ammo_count != 1 or medic_count != 1 or scout_count < 1 or scout_count > 3:
                ranked = False


        game.ranked = ranked

        # get last team standing

        alive_player_count = {}

        # quick lookup
        stats_by_entity_id = {
            stat.entity.entity_id: stat
            for stat in sm5_stats
        }

        # check who's alive
        for entity in entity_list:
            if entity.type != EntityType.PLAYER:
                continue

            stat = stats_by_entity_id.get(entity.entity_id)

            if stat and stat.lives_left > 0:
                color = entity.team.color_enum
                alive_player_count[color] = alive_player_count.get(color, 0) + 1

        # if only one team has living players, they won by elim
        if len(alive_player_count) == 1:
            winning_color = next(iter(alive_player_count))

            entity_start = next(
                (
                    entity for entity in entity_list
                    if entity.team and entity.team.color_enum == winning_color
                ),
                None,
            )

            if entity_start:
                game.last_team_standing = entity_start.team

        # doubles %

        for team in teams.values():
            team.doubles_percent = async_to_sync(game.calculate_team_doubles_percent)(team)
            team.save()

        # winner

        # get all teams in the game.
        teams_ = game.get_teams()

        scores = {team: team.score for team in teams_}

        if not scores:
            raise ValueError("Game has no teams")

        # adjust scores if a team was eliminated.
        if game.last_team_standing in scores:
            scores[game.last_team_standing] += 10000

        # determine the winner based on the updated scores.
        max_score = max(scores.values())
        winning_teams = [team for team, score in scores.items() if score == max_score]

        if len(winning_teams) == 1:
            winner = winning_teams[0]
        else: # Tie or no clear winner
            winner = None

        game.winner = winner

        # rankings

        if ranked and not skip_rankings:
            if update_sm5_rankings(game):
                logger.info(f"Updated player rankings for game {game.id}")
            else:
                logger.error(f"Failed to update player rankings for game {game.id}")
        else: # still need to add current_rating and previous_rating (nothing changed)
            # get all players ahead of time
            player_entity_ids = [
                entity.entity_id
                for entity in entity_list
                if entity.type == EntityType.PLAYER
            ]

            players_by_entity_id = {
                player.entity_id: player
                for player in Player.objects.filter(
                    entity_id__in=player_entity_ids
                )
            }

            for entity_end in entity_ends:
                entity = entity_end.entity

                if entity.type != EntityType.PLAYER:
                    continue

                entity_id = entity.entity_id

                if entity_id.startswith("@"):
                    entity_end.ratings = BLANK_ENTITY_RATING
                    continue

                player = players_by_entity_id.get(entity_id)

                try:
                    if player is None:
                        raise AttributeError("Player not found")

                    global_ratings = player.get_rating()
                    global_role_ratings = player.get_rating(
                        role=entity.role
                    )
                    site_ratings = player.get_rating(site=game.site_id)
                    site_role_ratings = player.get_rating(
                        role=entity.role,
                        site=game.site_id,
                    )

                    entity_end.ratings = {
                        "current": {
                            "global": {
                                "mu": global_ratings.mu,
                                "sigma": global_ratings.sigma,
                            },
                            "global_role": {
                                "mu": global_role_ratings.mu,
                                "sigma": global_role_ratings.sigma,
                            },
                            "site": {
                                "mu": site_ratings.mu,
                                "sigma": site_ratings.sigma,
                            },
                            "site_role": {
                                "mu": site_role_ratings.mu,
                                "sigma": site_role_ratings.sigma,
                            },
                        },
                        "previous": {
                            "global": {
                                "mu": global_ratings.mu,
                                "sigma": global_ratings.sigma,
                            },
                            "global_role": {
                                "mu": global_role_ratings.mu,
                                "sigma": global_role_ratings.sigma,
                            },
                            "site": {
                                "mu": site_ratings.mu,
                                "sigma": site_ratings.sigma,
                            },
                            "site_role": {
                                "mu": site_role_ratings.mu,
                                "sigma": site_role_ratings.sigma,
                            },
                        },
                    }

                except AttributeError:
                    entity_end.ratings = BLANK_ENTITY_RATING

            EntityEnd.objects.bulk_update(
                entity_ends,
                ["ratings"],
                batch_size=1000,
            )

        game.save()
    except Exception as e:
        logger.error(f"Error processing game {file_location.name} or got cut off. Deleting...")
        traceback.print_exc()
        raise
        

def process_laserball(
    file_location: Path,
    site_id: str,
    tdf_name: str,
    file_version: str,
    software_version: str,
    mission_type: int,
    mission_name: str,
    force_ended_early: bool,
    start_time: str,
    mission_duration: timedelta,
    duration: timedelta,
    penalty_amount: int,
    teams: List[Team],
    entity_starts: List[EntityStart],
    events: List[Event],
    scores: List[ScoreEvent],
    entity_ends: List[EntityEnd],
    player_states: List[PlayerState],
    step_events: List[StepEvent],
    shot_events: List[ShotEvent],
    life_events: List[LifeEvent],
    missile_events: List[MissileEvent],
    team1: Team,
    team2: Team,
    team1_size: int,
    team2_size: int,
    skip_rankings: bool = False
):
    # ranking
    pass
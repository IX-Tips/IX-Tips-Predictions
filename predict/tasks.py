import logging
from datetime import date

from celery import shared_task
from django.core.cache import cache

logger = logging.getLogger(__name__)

from .constants import COMPETITIONS, TRAINING_CACHE_TIMEOUT
from .models import MatchPrediction, TopPick
from .fixture_sources import fetch_fixture_matches_by_date
from .utils import (
    fetch_and_cache_team_metadata,
    fetch_matches_by_date,
    fetch_training_data_all_seasons,
    find_next_match_date,
    find_upcoming_match_dates,
    get_league_table,
    get_or_train_model_bundle,
    get_top_predictions_for_variant,
    save_predictions,
    store_top_pick_for_date
)

@shared_task
def schedule_predictions_staggered(match_date=None):
    delay = 0
    for comp in COMPETITIONS:
        logger.info(f" Scheduling prediction for {comp} in {delay} seconds")
        predict_next_fixtures_for_competition.apply_async(
            args=[comp, match_date],
            countdown=delay
        )
        delay += 180  # now using 2 minutes instead of 1

@shared_task
def trigger_staggered_scheduling():
    schedule_predictions_staggered.delay()

@shared_task
def predict_next_fixtures_for_competition(competition_code, match_date=None):
    from .views import refresh_competition_odds

    logger.info("Running prediction for %s on %s", competition_code, match_date or "auto")
    if not match_date:
        match_dates_to_use = find_upcoming_match_dates(
            fetch_fixture_matches_by_date, None, competition_code, days_ahead=30,
        )
    else:
        match_dates_to_use = [match_date]

    if not match_dates_to_use:
        result = {"competition": competition_code, "status": "no_fixtures", "training_entries": 0, "fixture_count": 0, "saved_predictions": 0, "dates_checked": []}
        logger.warning("PREDICTION_COVERAGE %s", result)
        return result

    df = cache.get(f"training_data_{competition_code}")
    if df is None:
        df = fetch_training_data_all_seasons(competition_code)
        cache.set(f"training_data_{competition_code}", df, timeout=TRAINING_CACHE_TIMEOUT)

    training_entries = len(df) if df is not None else 0
    if df is None or df.empty:
        result = {"competition": competition_code, "status": "no_training_data", "training_entries": training_entries, "fixture_count": 0, "saved_predictions": 0, "dates_checked": match_dates_to_use}
        logger.warning("PREDICTION_COVERAGE %s", result)
        return result

    model_bundle = get_or_train_model_bundle(competition_code)
    if model_bundle is None:
        result = {"competition": competition_code, "status": "model_unavailable", "training_entries": training_entries, "fixture_count": 0, "saved_predictions": 0, "dates_checked": match_dates_to_use}
        logger.warning("PREDICTION_COVERAGE %s", result)
        return result

    model_home, model_away, model_context = model_bundle
    total_saved = 0
    total_fixtures = 0
    dates_with_fixtures = []
    for match_date_to_use in match_dates_to_use:
        logger.info("Processing %s for %s", competition_code, match_date_to_use)
        matches = fetch_fixture_matches_by_date(None, competition_code, match_date_to_use)
        if not matches:
            logger.warning("No fixtures for %s on %s", competition_code, match_date_to_use)
            continue

        total_fixtures += len(matches)
        dates_with_fixtures.append(match_date_to_use)
        predictions = save_predictions(
            matches, model_home, model_away, model_context,
            match_date=match_date_to_use, competition_code=competition_code,
        )
        total_saved += len(predictions)
        refresh_competition_odds(
            competition_code, force=True,
            match_dates=[date.fromisoformat(match_date_to_use)],
        )

    result = {
        "competition": competition_code,
        "status": "generated" if total_saved else ("fixtures_found_no_predictions" if total_fixtures else "no_fixtures"),
        "training_entries": training_entries,
        "fixture_count": total_fixtures,
        "saved_predictions": total_saved,
        "dates_checked": match_dates_to_use,
        "dates_with_fixtures": dates_with_fixtures,
    }
    logger.info("PREDICTION_COVERAGE %s", result)
    return result

@shared_task
def cache_training_data():
    logger.info("Starting training data caching")
    for comp in COMPETITIONS:
        key = f"training_data_{comp}"
        df = fetch_training_data_all_seasons(comp)

        if df is not None and not df.empty:
            cache.set(key, df, timeout=TRAINING_CACHE_TIMEOUT)
            logger.info(f" Cached {len(df)} records for {comp}")
        else:
            logger.info(f" No data fetched for {comp}. Check prior API error logs for auth/network failures.")


@shared_task
def refresh_all_league_tables():
    for code in COMPETITIONS:
        logger.info(f" Refreshing league table for {code}")
        get_league_table(code)

@shared_task
def update_metadata_task():
    fetch_and_cache_team_metadata()

@shared_task
def store_daily_top_pick():
    stored = {}
    for variant in ("1", "2", "3", "4"):
        # (delete now handled atomically inside store_top_pick_for_date)
        predictions = get_top_predictions_for_variant(limit=(20 if variant == "4" else 10), variant=variant)
        stored[variant] = store_top_pick_for_date(predictions, variant=variant)
    return stored

@shared_task
def refresh_daily_odds_cache():
    from .views import update_all_odds

    updated = update_all_odds()
    stored_top_picks = {}
    for variant in ("1", "2", "3", "4"):
        # (delete now handled atomically inside store_top_pick_for_date)
        top_predictions = get_top_predictions_for_variant(limit=(20 if variant == "4" else 10), variant=variant)
        stored_top_picks[variant] = store_top_pick_for_date(top_predictions, variant=variant)
    return {
        "odds_updates": updated,
        "stored_top_picks": stored_top_picks,
    }

@shared_task
def refresh_live_match_data():
    """
    Refresh live match status, actual scores, cached kickoff metadata, odds, and top picks.
    Runs in the background so the UI does not need to do the expensive refresh itself.
    """
    from .views import refresh_prediction_statuses

    from datetime import timedelta
    today = date.today()
    yesterday = today - timedelta(days=1)
    refresh_pairs = (
        MatchPrediction.objects.filter(match_date__gte=yesterday)
        .values("match_date", "competition")
        .distinct()
    )

    status_updates = 0
    for entry in refresh_pairs:
        match_date = entry["match_date"]
        competition = entry["competition"]
        if not competition or not match_date:
            continue
        status_updates += refresh_prediction_statuses(competition, match_date, force=True)

    stored_top_picks = {}
    for variant in ("1", "2", "3", "4"):
        # (delete now handled atomically inside store_top_pick_for_date)
        top_predictions = get_top_predictions_for_variant(limit=(20 if variant == "4" else 10), variant=variant)
        stored_top_picks[variant] = store_top_pick_for_date(top_predictions, variant=variant)

    return {
        "status_updates": status_updates,
        "stored_top_picks": stored_top_picks,
        "dates_checked": [str(entry["match_date"]) for entry in refresh_pairs],
    }


@shared_task
def refresh_combo_slips():
    from .views import generate_all_combo_slips

    return generate_all_combo_slips()

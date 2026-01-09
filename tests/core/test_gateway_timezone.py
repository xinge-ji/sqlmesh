"""Tests for gateway timezone configuration."""
import pytest
from datetime import date, datetime, timezone
from sqlmesh.core.config import Config, GatewayConfig, DuckDBConnectionConfig
from sqlmesh.core.context import Context
from sqlmesh.utils.concurrency import concurrent_apply_to_values
from sqlmesh.utils.cron import CroniterCache
from sqlmesh.utils.date import date_dict, now, set_timezone, get_timezone, yesterday, yesterday_ds
from tempfile import TemporaryDirectory
from pathlib import Path


def test_gateway_timezone_configuration():
    """Test that gateway timezone can be configured."""
    config = Config(
        gateways={
            "my_gateway": GatewayConfig(
                timezone="America/New_York",
                connection=DuckDBConnectionConfig(),
            )
        }
    )
    
    assert config.get_timezone("my_gateway") == "America/New_York"


def test_gateway_timezone_default_none():
    """Test that gateway timezone defaults to None when not specified."""
    config = Config(
        gateways={
            "my_gateway": GatewayConfig(
                connection=DuckDBConnectionConfig(),
            )
        }
    )
    
    assert config.get_timezone("my_gateway") is None


def test_now_with_timezone():
    """Test that now() function respects timezone parameter."""
    # Get current time in UTC
    utc_time = now(tz=None)
    
    # Get current time in New York (EST/EDT)
    ny_time = now(tz="America/New_York")
    
    # Both should represent roughly the same moment in time (within a second)
    # when converted to UTC
    utc_time_tz = utc_time.replace(tzinfo=timezone.utc)
    ny_time_utc = ny_time.astimezone(timezone.utc)
    
    time_diff = abs((utc_time_tz - ny_time_utc).total_seconds())
    assert time_diff < 2, f"Time difference too large: {time_diff} seconds"
    
    # But the hour should be different between them
    # (unless we're testing exactly at the boundary where they align)
    # This is a weak assertion but checks that timezone is being applied
    assert utc_time.tzinfo != ny_time.tzinfo


def test_context_gateway_timezone():
    """Test that Context correctly exposes gateway timezone."""
    original_tz = get_timezone()
    with TemporaryDirectory() as tmpdir:
        # Create a simple model file
        models_dir = Path(tmpdir) / "models"
        models_dir.mkdir()
        
        model_file = models_dir / "test_model.sql"
        model_file.write_text("""
MODEL (
  name test_model,
  kind FULL
);

SELECT 1 as id
""")
        
        config = Config(
            gateways={
                "test_gateway": GatewayConfig(
                    timezone="America/Los_Angeles",
                    connection=DuckDBConnectionConfig(),
                )
            },
            default_gateway="test_gateway"
        )
        
        context = Context(paths=tmpdir, config=config, load=True)
        
        assert context.gateway_timezone == "America/Los_Angeles"
        assert get_timezone() == "America/Los_Angeles"
    set_timezone(original_tz)


def test_invalid_timezone_fallback():
    """Test that invalid timezone falls back to UTC."""
    # now() should not raise an error with invalid timezone, just use UTC
    result = now(tz="Invalid/Timezone")
    
    # Should return a datetime object with UTC timezone
    assert result.tzinfo is not None
    # The result should have UTC offset (0)
    assert result.utcoffset().total_seconds() == 0


def test_multiple_gateways_with_different_timezones():
    """Test that different gateways can have different timezones."""
    config = Config(
        gateways={
            "ny_gateway": GatewayConfig(
                timezone="America/New_York",
                connection=DuckDBConnectionConfig(),
            ),
            "la_gateway": GatewayConfig(
                timezone="America/Los_Angeles",
                connection=DuckDBConnectionConfig(),
            ),
            "utc_gateway": GatewayConfig(
                timezone=None,  # Should default to UTC
                connection=DuckDBConnectionConfig(),
            ),
        }
    )
    
    assert config.get_timezone("ny_gateway") == "America/New_York"
    assert config.get_timezone("la_gateway") == "America/Los_Angeles"
    assert config.get_timezone("utc_gateway") is None


def test_timezone_context_variable():
    """Test that set_timezone/get_timezone works with context variables."""
    # Save current timezone
    original_tz = get_timezone()
    
    try:
        # Test setting and getting timezone
        set_timezone("America/Chicago")
        assert get_timezone() == "America/Chicago"
        
        # now() should use the context timezone
        chicago_time = now()
        assert chicago_time.tzinfo.key == "America/Chicago"
        
        # Change timezone
        set_timezone("Europe/Paris")
        assert get_timezone() == "Europe/Paris"
        
        paris_time = now()
        assert paris_time.tzinfo.key == "Europe/Paris"
        
        # Reset to None (UTC)
        set_timezone(None)
        assert get_timezone() is None
        utc_time = now()
        assert utc_time.tzinfo == timezone.utc
    finally:
        # Restore original timezone
        set_timezone(original_tz)


def test_all_date_functions_use_context_timezone():
    """Test that all date utility functions respect the context timezone."""
    original_tz = get_timezone()
    
    try:
        # Set timezone context
        set_timezone("America/New_York")
        
        # All these functions should now use NY time
        ny_now = now()
        ny_yesterday = yesterday()
        ny_yesterday_str = yesterday_ds()
        
        # Verify they're all in NY timezone
        assert ny_now.tzinfo.key == "America/New_York"
        assert ny_yesterday.tzinfo.key == "America/New_York"
        
        # Verify yesterday is actually one day before
        assert (ny_now.date() - ny_yesterday.date()).days == 1
        
    finally:
        set_timezone(original_tz)


def test_date_dict_respects_context_timezone():
    original_tz = get_timezone()

    try:
        set_timezone("America/Los_Angeles")

        # 2024-01-01 00:30 UTC is still 2023-12-31 in Los Angeles.
        execution_time = datetime(2024, 1, 1, 0, 30, tzinfo=timezone.utc)
        variables = date_dict(execution_time=execution_time, start=None, end=None)

        assert variables["execution_ds"] == "2023-12-31"
        assert variables["execution_date"] == date(2023, 12, 31)
        assert variables["execution_dt"].tzinfo.key == "America/Los_Angeles"
    finally:
        set_timezone(original_tz)


def test_croniter_cache_respects_context_timezone():
    original_tz = get_timezone()

    try:
        set_timezone("America/Los_Angeles")

        base_time = datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc)  # 2023-12-31 17:00 in LA
        cron = CroniterCache("0 0 * * *", time=base_time)
        next_run = cron.get_next()

        assert next_run.tzinfo.key == "America/Los_Angeles"
        assert next_run.hour == 0
        assert next_run.minute == 0
        assert next_run.date() == date(2024, 1, 1)
    finally:
        set_timezone(original_tz)


def test_timezone_propagates_to_threadpool():
    original_tz = get_timezone()

    try:
        set_timezone("Europe/Paris")

        results = concurrent_apply_to_values([1, 2, 3], lambda _: get_timezone(), tasks_num=3)
        assert results == ["Europe/Paris", "Europe/Paris", "Europe/Paris"]
    finally:
        set_timezone(original_tz)

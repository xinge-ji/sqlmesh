# Gateway Timezone Feature Implementation

## Summary

This implementation adds timezone support for SQLMesh gateways, allowing all time/date operations in SQLMesh to follow the timezone configured for each gateway.

## Architecture

The implementation uses Python's `contextvars` module to provide thread-safe, context-aware timezone handling throughout SQLMesh. This allows all date utility functions to automatically respect the gateway's timezone without requiring explicit timezone parameters to be passed through every function call.

### Key Components

1. **Context Variable for Timezone** (`sqlmesh/utils/date.py`)
   - Added `_current_timezone` context variable to store the active timezone
   - Added `set_timezone()` and `get_timezone()` helper functions
   - Modified `now()` to automatically use the context timezone when no explicit timezone is provided

2. **Gateway Configuration** (`sqlmesh/core/config/gateway.py`)
   - Added `timezone` field to `GatewayConfig` class
   - Accepts IANA timezone names (e.g., 'America/New_York', 'Europe/London')
   - Defaults to UTC if not specified

3. **Config Integration** (`sqlmesh/core/config/root.py`)
   - Added `get_timezone()` method to retrieve timezone for a specific gateway

4. **Context Integration** (`sqlmesh/core/context.py`)
   - Added `gateway_timezone` property to access the selected gateway's timezone
   - Context automatically calls `set_timezone()` during initialization
   - This ensures all subsequent date operations use the gateway's timezone

5. **Documentation**
   - Updated `docs/reference/configuration.md` with timezone configuration reference
   - Updated `docs/guides/configuration.md` with timezone usage examples and best practices
   - Created example configuration file `examples/gateway_timezone_example.yaml`

6. **Tests** (`tests/core/test_gateway_timezone.py`)
   - Tests for timezone configuration
   - Tests for context variable behavior
   - Tests for automatic timezone application across all date functions

## How It Works

### Configuration Example

```yaml
gateways:
  my_gateway:
    timezone: America/New_York  # All operations use NY time
    connection:
      type: duckdb
      database: my_db.db
```

### Execution Flow

1. User creates a Context with a gateway that has a timezone configured
2. Context reads the gateway configuration and calls `set_timezone(gateway_timezone)`
3. The timezone is stored in a thread-local context variable
4. All subsequent calls to date utility functions (like `now()`, `yesterday()`, etc.) automatically use this timezone
5. When `now()` is called without an explicit timezone parameter, it checks the context variable and uses that timezone

### Benefits of Context Variable Approach

- **Thread-safe**: Each thread/async context has its own timezone setting
- **Transparent**: Functions don't need timezone parameters passed explicitly
- **Backwards compatible**: Existing code works without modification
- **Comprehensive**: All date functions automatically respect the gateway timezone

## Usage Examples

### Basic Configuration

```yaml
gateways:
  production:
    timezone: America/New_York
    connection:
      type: snowflake
      account: my_account
      
  development:
    timezone: America/Los_Angeles
    connection:
      type: duckdb
```

### Python API

```python
from sqlmesh import Context
from sqlmesh.core.config import Config, GatewayConfig, DuckDBConnectionConfig

config = Config(
    gateways={
        "my_gateway": GatewayConfig(
            timezone="Europe/London",
            connection=DuckDBConnectionConfig(),
        )
    }
)

context = Context(paths="my_project", config=config)
# All time operations in this context now use London time
```

### Direct Timezone Control

```python
from sqlmesh.utils.date import set_timezone, get_timezone, now

# Set timezone for current context
set_timezone("Asia/Tokyo")

# All date functions now use Tokyo time
tokyo_time = now()
print(f"Current time in Tokyo: {tokyo_time}")

# Check current timezone
print(f"Active timezone: {get_timezone()}")
```

## Important Notes

### How Gateway Timezone Affects Your Data

The gateway timezone affects **ALL** time/date operations in SQLMesh:

1. **Job Scheduling**: When jobs are scheduled to run
2. **Time Ranges in Queries**: The values of `@start_ds`, `@end_ds`, `@start_ts`, `@end_ts`, etc. in your models
3. **Relative Time Expressions**: How "yesterday", "today", "1 week ago" are interpreted
4. **Data Filtering**: Time ranges used for incremental model backfills and updates

### Working with Local Timezones

**You do NOT need to convert your data to UTC.** The gateway timezone allows you to work with data in your local timezone:

- Your model's `time_column` can store timestamps in the gateway timezone
- `@start_ds` and `@end_ds` represent dates in the gateway timezone
- All time-based filtering uses the gateway timezone
- Date boundaries (like "yesterday", "today") align with the gateway timezone, not UTC

### Example: New York Timezone

If you configure `timezone: America/New_York`:

```sql
MODEL (
  name my_schema.events,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column event_timestamp  -- This can be in NY time
  )
);

SELECT 
  event_timestamp,  -- Stored in NY time
  event_data
FROM raw_events
WHERE 
  event_timestamp BETWEEN @start_ds AND @end_ds  -- NY time ranges
```

When SQLMesh processes this:
- If today is 2024-01-15 in New York, `@start_ds` = '2024-01-15' (NY time, not UTC)
- The job runs according to New York business hours
- Date boundaries align with New York days, not UTC days

### When to Use UTC vs Local Time

**Use Local Timezone** (gateway timezone):
- When your business operates in a specific timezone
- When "yesterday" should mean yesterday in your business timezone
- When you want to avoid timezone conversion complexity
- When your data warehouse already stores data in local time

**Use UTC** (don't set gateway timezone):
- When working with global data across multiple timezones
- When you need timezone-agnostic processing
- When following strict UTC-only data engineering practices

## Files Modified

- `sqlmesh/core/config/gateway.py` - Added timezone field
- `sqlmesh/core/config/root.py` - Added get_timezone() method
- `sqlmesh/core/context.py` - Added gateway_timezone property and set_timezone() call
- `sqlmesh/utils/date.py` - Added context variable support
- `docs/reference/configuration.md` - Added timezone documentation
- `docs/guides/configuration.md` - Added timezone usage guide
- `examples/gateway_timezone_example.yaml` - Example configuration
- `tests/core/test_gateway_timezone.py` - Comprehensive tests

## Testing

Run the timezone tests:
```bash
pytest tests/core/test_gateway_timezone.py -v
```

## Backward Compatibility

This feature is fully backward compatible:
- If no timezone is specified, defaults to UTC (current behavior)
- All existing code continues to work without modification
- The timezone parameter on date functions is optional and overrides the context timezone when provided


-- Example: Working with data in New York timezone
-- 
-- This example assumes:
-- 1. Gateway is configured with timezone: America/New_York
-- 2. Your raw data has timestamps in New York time
-- 3. You want to process data by New York business days
--
-- With gateway timezone set, you DON'T need to convert to UTC!

MODEL (
  name my_schema.daily_events,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column event_date  -- This is in NY time, not UTC
  ),
  cron '@daily',  -- Runs daily at midnight New York time
  owner 'data_team'
);

-- Process events for the day
-- @start_ds and @end_ds are in New York time
SELECT 
  event_date,
  event_id,
  user_id,
  event_type,
  revenue,
  CURRENT_TIMESTAMP AS processed_at  -- This will be in NY time too!
FROM raw_schema.events
WHERE 
  -- Filter by the NY time range for this interval
  event_date BETWEEN @start_ds AND @end_ds
  AND event_date < CURRENT_DATE  -- Only process completed NY days
  
-- Example data:
-- If today is 2024-01-15 in New York (10am):
-- - @start_ds = '2024-01-15' (NY time)
-- - @end_ds = '2024-01-15' (NY time)
-- - CURRENT_DATE returns 2024-01-15 (NY date, not UTC date)
--
-- This means:
-- - You process data for NY business day 2024-01-15
-- - Even though it might still be 2024-01-14 in UTC
-- - Your data doesn't need timezone conversion



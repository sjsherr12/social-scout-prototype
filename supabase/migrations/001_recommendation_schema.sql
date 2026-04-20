-- ============================================================================
-- Social Scout — Recommendation Backend Schema
-- ============================================================================
-- Run this in the Supabase SQL editor (or psql against your project).
-- Idempotent: safe to re-run. Extends the existing `profiles` table.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
create extension if not exists "uuid-ossp";
-- pg_cron is optional for MVP — admin endpoints can trigger jobs manually.
-- Uncomment on Supabase Pro+ if you want scheduled jobs:
-- create extension if not exists pg_cron;


-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
do $$ begin
  create type city_status as enum ('seeding', 'active');
exception when duplicate_object then null; end $$;

do $$ begin
  create type anchor_status as enum ('pending', 'in_progress', 'done', 'invalid');
exception when duplicate_object then null; end $$;

do $$ begin
  create type profile_analysis_state as enum ('qualified', 'disqualified');
exception when duplicate_object then null; end $$;

do $$ begin
  create type queue_status as enum ('queued', 'in_progress', 'done', 'failed');
exception when duplicate_object then null; end $$;


-- ---------------------------------------------------------------------------
-- cities
-- ---------------------------------------------------------------------------
create table if not exists cities (
  id uuid primary key default uuid_generate_v4(),
  name text not null,
  state text not null,
  country text not null default 'US',
  lat numeric,
  lng numeric,
  status city_status not null default 'seeding',
  created_at timestamptz not null default now(),
  unique (name, state, country)
);


-- ---------------------------------------------------------------------------
-- anchor_locations
-- ---------------------------------------------------------------------------
create table if not exists anchor_locations (
  id uuid primary key default uuid_generate_v4(),
  city_id uuid not null references cities(id) on delete cascade,
  username text not null,
  display_name text,
  category text,
  follower_count integer,
  status anchor_status not null default 'pending',
  followers_fetched_at timestamptz,
  invalid_reason text,
  created_at timestamptz not null default now(),
  unique (city_id, username)
);

create index if not exists anchor_locations_city_status_idx
  on anchor_locations (city_id, status, follower_count desc);


-- ---------------------------------------------------------------------------
-- profiles — extend existing table
-- ---------------------------------------------------------------------------
alter table profiles
  add column if not exists city_id uuid references cities(id) on delete set null;

alter table profiles
  add column if not exists analysis_state profile_analysis_state
    not null default 'qualified';

alter table profiles
  add column if not exists disqualification_reason text;

alter table profiles
  add column if not exists analyzed_at timestamptz;

create index if not exists profiles_city_state_idx
  on profiles (city_id, analysis_state);


-- ---------------------------------------------------------------------------
-- analysis_queue
-- ---------------------------------------------------------------------------
create table if not exists analysis_queue (
  id bigserial primary key,
  follower_username text not null,
  city_id uuid not null references cities(id) on delete cascade,
  anchor_id uuid references anchor_locations(id) on delete set null,
  status queue_status not null default 'queued',
  attempts integer not null default 0,
  last_error text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);

create index if not exists analysis_queue_status_id_idx
  on analysis_queue (status, id);

create index if not exists analysis_queue_city_status_idx
  on analysis_queue (city_id, status);


-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------
create table if not exists users (
  id uuid primary key default uuid_generate_v4(),
  email text unique,
  name text,
  city_id uuid references cities(id) on delete set null,
  lat numeric,
  lng numeric,
  first_eligible_drop_at timestamptz,
  created_at timestamptz not null default now()
);


-- ---------------------------------------------------------------------------
-- user_recommendations
-- ---------------------------------------------------------------------------
create table if not exists user_recommendations (
  user_id uuid not null references users(id) on delete cascade,
  profile_username text not null,
  drop_date date not null,
  rank integer not null,
  created_at timestamptz not null default now(),
  primary key (user_id, profile_username)
);

create index if not exists user_recs_user_drop_idx
  on user_recommendations (user_id, drop_date);


-- ---------------------------------------------------------------------------
-- haversine — great-circle distance in miles between two lat/lng points
-- ---------------------------------------------------------------------------
create or replace function haversine_miles(
  lat1 numeric, lng1 numeric, lat2 numeric, lng2 numeric
) returns numeric
language sql
immutable
as $$
  select 3958.8 * 2 * asin(sqrt(
    sin(radians((lat2 - lat1) / 2)) ^ 2 +
    cos(radians(lat1)) * cos(radians(lat2)) *
    sin(radians((lng2 - lng1) / 2)) ^ 2
  ));
$$;


-- ---------------------------------------------------------------------------
-- curate_drop_for_user
-- ---------------------------------------------------------------------------
-- Returns up to 10 candidate profile usernames for a user's next drop.
-- Home-city first, cross-city fallback ordered by distance. Excludes
-- anything already in user_recommendations for this user.
-- Set dry_run=true to just return candidates without inserting.
-- ---------------------------------------------------------------------------
create or replace function curate_drop_for_user(
  p_user_id uuid,
  p_drop_date date default current_date,
  p_dry_run boolean default false
) returns table (
  profile_username text,
  city_id uuid,
  distance_rank integer,
  attractiveness_rating numeric,
  rank integer
)
language plpgsql
as $$
begin
  -- Validate user exists (clearer error than silent empty result).
  if not exists (select 1 from users where id = p_user_id) then
    raise exception 'User % not found', p_user_id;
  end if;

  -- Non-dry-run: insert the top 10 candidates for this drop.
  if not p_dry_run then
    insert into user_recommendations (user_id, profile_username, drop_date, rank)
    select p_user_id, sub.profile_username, p_drop_date, sub.rnk
    from (
      select
        pl.profile_username,
        row_number() over (
          order by pl.distance_rank asc,
                   pl.attractiveness_rating * (1 + (random() - 0.5) * 0.2) desc
        )::integer as rnk
      from (
        -- Home city
        select
          p.username as profile_username,
          0 as distance_rank,
          coalesce(p.attractiveness_rating, 0)::numeric as attractiveness_rating
        from profiles p, users u
        where u.id = p_user_id
          and p.city_id = u.city_id
          and p.analysis_state = 'qualified'
          and not exists (
            select 1 from user_recommendations ur
            where ur.user_id = p_user_id and ur.profile_username = p.username
          )
        union all
        -- Cross-city fallback sorted outward by distance
        select
          p.username,
          dense_rank() over (
            order by haversine_miles(c.lat, c.lng, u.lat, u.lng)
          )::integer as distance_rank,
          coalesce(p.attractiveness_rating, 0)::numeric
        from profiles p
        join cities c on c.id = p.city_id
        cross join users u
        where u.id = p_user_id
          and p.city_id <> u.city_id
          and p.analysis_state = 'qualified'
          and c.lat is not null and c.lng is not null
          and u.lat is not null and u.lng is not null
          and not exists (
            select 1 from user_recommendations ur
            where ur.user_id = p_user_id and ur.profile_username = p.username
          )
      ) pl
    ) sub
    where sub.rnk <= 10
    on conflict (user_id, profile_username) do nothing;

    -- Return what was just inserted for this drop.
    return query
    select
      ur.profile_username,
      p.city_id,
      0::integer as distance_rank,
      coalesce(p.attractiveness_rating, 0)::numeric,
      ur.rank
    from user_recommendations ur
    left join profiles p on p.username = ur.profile_username
    where ur.user_id = p_user_id
      and ur.drop_date = p_drop_date
    order by ur.rank;
    return;
  end if;

  -- Dry-run: return candidates that WOULD be picked, without inserting.
  return query
  select
    sub.profile_username,
    sub.city_id,
    sub.distance_rank,
    sub.attractiveness_rating,
    sub.rnk as rank
  from (
    select
      pl.profile_username,
      pl.city_id,
      pl.distance_rank,
      pl.attractiveness_rating,
      row_number() over (
        order by pl.distance_rank asc,
                 pl.attractiveness_rating * (1 + (random() - 0.5) * 0.2) desc
      )::integer as rnk
    from (
      select
        p.username as profile_username,
        p.city_id,
        0 as distance_rank,
        coalesce(p.attractiveness_rating, 0)::numeric as attractiveness_rating
      from profiles p, users u
      where u.id = p_user_id
        and p.city_id = u.city_id
        and p.analysis_state = 'qualified'
        and not exists (
          select 1 from user_recommendations ur
          where ur.user_id = p_user_id and ur.profile_username = p.username
        )
      union all
      select
        p.username,
        p.city_id,
        dense_rank() over (
          order by haversine_miles(c.lat, c.lng, u.lat, u.lng)
        )::integer as distance_rank,
        coalesce(p.attractiveness_rating, 0)::numeric
      from profiles p
      join cities c on c.id = p.city_id
      cross join users u
      where u.id = p_user_id
        and p.city_id <> u.city_id
        and p.analysis_state = 'qualified'
        and c.lat is not null and c.lng is not null
        and u.lat is not null and u.lng is not null
        and not exists (
          select 1 from user_recommendations ur
          where ur.user_id = p_user_id and ur.profile_username = p.username
        )
    ) pl
  ) sub
  where sub.rnk <= 10;
end;
$$;


-- ---------------------------------------------------------------------------
-- next_drop_timestamp — returns the next Mon/Thu 5pm America/New_York after now()
-- ---------------------------------------------------------------------------
create or replace function next_drop_timestamp() returns timestamptz
language plpgsql
immutable
as $$
declare
  now_et timestamp;
  candidate timestamptz;
  dow integer;
  days_ahead integer;
begin
  now_et := (now() at time zone 'America/New_York')::timestamp;

  -- Try each of the next 8 days
  for days_ahead in 0..7 loop
    candidate := ((date_trunc('day', now_et) + (days_ahead || ' days')::interval
                    + interval '17 hours')
                    at time zone 'America/New_York')::timestamptz;
    dow := extract(dow from (candidate at time zone 'America/New_York'));
    -- dow: 0=Sun, 1=Mon, 4=Thu
    if (dow = 1 or dow = 4) and candidate > now() then
      return candidate;
    end if;
  end loop;

  return null;
end;
$$;


-- ---------------------------------------------------------------------------
-- compute_first_eligible_drop — for a signup at p_signup_time, the next
-- Mon/Thu 5pm ET that is at least 6 hours away.
-- ---------------------------------------------------------------------------
create or replace function compute_first_eligible_drop(p_signup_time timestamptz)
returns timestamptz
language plpgsql
immutable
as $$
declare
  candidate timestamptz;
  days_ahead integer;
  dow integer;
  signup_et timestamp;
begin
  signup_et := (p_signup_time at time zone 'America/New_York')::timestamp;

  for days_ahead in 0..10 loop
    candidate := ((date_trunc('day', signup_et) + (days_ahead || ' days')::interval
                    + interval '17 hours')
                    at time zone 'America/New_York')::timestamptz;
    dow := extract(dow from (candidate at time zone 'America/New_York'));
    if (dow = 1 or dow = 4) and candidate >= p_signup_time + interval '6 hours' then
      return candidate;
    end if;
  end loop;

  return null;
end;
$$;

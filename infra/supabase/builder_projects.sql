-- Builder-Prod: project metadata table
-- Run once in Supabase SQL editor (new project, service role key in .env.local)

create table if not exists builder_projects (
  id          uuid primary key default gen_random_uuid(),
  user_id     text not null,
  name        text not null,
  location    text,
  status      text not null default 'needs-review',
  boundary    jsonb,
  coordinates text,
  area_sqm    numeric,
  created_at  timestamptz not null default now()
);

create index if not exists idx_builder_projects_user on builder_projects (user_id);

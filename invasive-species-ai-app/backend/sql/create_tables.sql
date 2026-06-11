-- Esquema completo do Supabase para o projeto Invasive Species AI
-- Copia para o SQL Editor do Supabase e executa para criar as tabelas.

-- Tabelas de lookup normalizadas
create table if not exists public.species (
  id              serial primary key,
  scientific_name text not null unique,
  common_name     text,
  family          text,
  origin_region   text
);

create table if not exists public.municipality (
  id       serial primary key,
  name     text not null unique,
  district text,
  area_km2 real
);

create table if not exists public.data_source (
  id          serial primary key,
  name        text not null unique,
  source_type text
);

-- Observações de campo (substitui records.json)
create table if not exists public.observation (
  id                serial primary key,
  species_id        integer not null references public.species(id),
  municipality_id   integer not null references public.municipality(id),
  source_id         integer not null references public.data_source(id),
  latitude          real    not null,
  longitude         real    not null,
  observed_on       date    not null,
  notes             text,
  validation_status text    default 'pending'
    check (validation_status = any (array['pending', 'validated', 'rejected']))
);

-- Relatórios gerados pelo agente
create table if not exists public.report (
  id               serial primary key,
  generated_at     timestamp default current_timestamp,
  source_type      text not null
    check (source_type = any (array['langchain_agent', 'template_fallback'])),
  content          text not null,
  validation_result text
);

-- Estatísticas raster pré-calculadas (populadas por scripts/build_raster_stats.py)
create table if not exists public.raster_statistics (
  id                bigint generated always as identity primary key,
  species_name      text    not null,
  period            text    not null,
  scenario          text,
  suitable_area_km2 numeric,
  mean_suitability  numeric,
  max_suitability   numeric,
  raster_file       text,
  created_at        timestamptz default now()
);

-- Cache de tiles PNG (metadados; os ficheiros PNG ficam em static/tiles/)
create table if not exists public.raster_cache (
  id           bigint generated always as identity primary key,
  species_name text not null,
  period       text not null,
  scenario     text,
  colormap     text,
  png_filename text,
  bounds       jsonb,
  created_at   timestamptz default now()
);

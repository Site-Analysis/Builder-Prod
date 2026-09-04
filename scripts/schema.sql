-- CockroachDB schema for builder_prod cadastral data.
-- Run once against a fresh cluster: cockroach sql --url $DATABASE_URL < scripts/schema.sql

CREATE DATABASE IF NOT EXISTS builder_prod;
USE builder_prod;

CREATE TABLE IF NOT EXISTS districts (
    dist_code  INT  PRIMARY KEY,
    name       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS taluks (
    dist_code  INT  NOT NULL REFERENCES districts (dist_code),
    taluk_code INT  NOT NULL,
    name       TEXT NOT NULL,
    PRIMARY KEY (dist_code, taluk_code)
);

CREATE TABLE IF NOT EXISTS hoblis (
    dist_code  INT  NOT NULL,
    taluk_code INT  NOT NULL,
    hobli_code INT  NOT NULL,
    name       TEXT NOT NULL,
    PRIMARY KEY (dist_code, taluk_code, hobli_code),
    FOREIGN KEY (dist_code, taluk_code) REFERENCES taluks (dist_code, taluk_code)
);

CREATE TABLE IF NOT EXISTS villages (
    dist_code  INT,
    taluk_code INT,
    hobli_code INT,
    vlg_code   INT,
    name       TEXT,
    lgd_code   INT,
    PRIMARY KEY (dist_code, taluk_code, hobli_code, vlg_code)
);

CREATE TABLE IF NOT EXISTS parcels (
    id             INT         DEFAULT unique_rowid() PRIMARY KEY,
    village_code   TEXT        NOT NULL,
    survey_no      TEXT        NOT NULL,
    survey_no_norm TEXT        NOT NULL,
    geom           GEOMETRY    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_parcels_village  ON parcels (village_code);
CREATE INDEX IF NOT EXISTS idx_parcels_survey   ON parcels (survey_no_norm);
CREATE INVERTED INDEX IF NOT EXISTS idx_parcels_geom ON parcels USING GIST (geom);

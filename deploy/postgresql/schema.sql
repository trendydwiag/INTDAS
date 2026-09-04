-- ============================================================
-- SPSE Crawler — PostgreSQL Schema
-- Compatible with Django 5.0 + psycopg2
-- Generated from Django models (web app, migration 0005)
-- ============================================================
-- USAGE:
--   1. Buat database:  createdb spse_crawler
--   2. Jalankan:       psql -U spse_user -d spse_crawler -f schema.sql
--   3. Seed data:      psql -U spse_user -d spse_crawler -f seed.sql
-- ============================================================

-- ============================================================
-- 0. EXTENSIONS
-- ============================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. web_kbli_master
--    KBLI code reference table — drives crawler matching &
--    dashboard multi-select filters.
-- ============================================================
CREATE TABLE IF NOT EXISTS web_kbli_master (
    code        VARCHAR(10)   PRIMARY KEY,
    name        VARCHAR(300)  NOT NULL DEFAULT '',
    is_active   BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kbli_master_is_active
    ON web_kbli_master (is_active);

COMMENT ON TABLE  web_kbli_master          IS 'KBLI reference codes — managed via CRUD API & admin';
COMMENT ON COLUMN web_kbli_master.code     IS '5-digit KBLI code, e.g. 62019';
COMMENT ON COLUMN web_kbli_master.name     IS 'Human-readable KBLI description';
COMMENT ON COLUMN web_kbli_master.is_active IS 'TRUE = included in crawler scoring & dashboard filters';

-- ============================================================
-- 2. web_tenderresult
--    Main tender data scraped from SPSE Inaproc.
--    Unique on (kode_instansi, id_lelang).
-- ============================================================
CREATE TABLE IF NOT EXISTS web_tenderresult (
    id                  BIGSERIAL       PRIMARY KEY,
    kode_instansi       VARCHAR(50)     NOT NULL,
    id_lelang           VARCHAR(100)    NOT NULL,
    nama_paket          TEXT            NOT NULL DEFAULT '',
    instansi            TEXT            NOT NULL DEFAULT '',
    satuan_kerja        VARCHAR(300)    NOT NULL DEFAULT '',
    hps                 BIGINT          NOT NULL DEFAULT 0,
    jenis_pengadaan     VARCHAR(200)    NOT NULL DEFAULT '',
    tahap_saat_ini      VARCHAR(200)    NOT NULL DEFAULT '',
    is_prakualifikasi   BOOLEAN         NOT NULL DEFAULT FALSE,
    kbli_code           VARCHAR(10)     NOT NULL DEFAULT '',
    kbli_description    VARCHAR(300)    NOT NULL DEFAULT '',
    is_it_priority      BOOLEAN         NOT NULL DEFAULT FALSE,
    priority_score      INTEGER         NOT NULL DEFAULT 0,
    url_pengumuman      TEXT            NOT NULL DEFAULT '',
    scraped_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_tenderresult_instansi_lelang
        UNIQUE (kode_instansi, id_lelang)
);

-- Indexes for sidebar filters & tab queries
CREATE INDEX IF NOT EXISTS idx_tender_kode_instansi
    ON web_tenderresult (kode_instansi);

CREATE INDEX IF NOT EXISTS idx_tender_id_lelang
    ON web_tenderresult (id_lelang);

CREATE INDEX IF NOT EXISTS idx_tender_jenis_pengadaan
    ON web_tenderresult (jenis_pengadaan);

CREATE INDEX IF NOT EXISTS idx_tender_tahap
    ON web_tenderresult (tahap_saat_ini);

CREATE INDEX IF NOT EXISTS idx_tender_is_prakualifikasi
    ON web_tenderresult (is_prakualifikasi);

CREATE INDEX IF NOT EXISTS idx_tender_kbli_code
    ON web_tenderresult (kbli_code);

CREATE INDEX IF NOT EXISTS idx_tender_is_it_priority
    ON web_tenderresult (is_it_priority);

CREATE INDEX IF NOT EXISTS idx_tender_priority_score
    ON web_tenderresult (priority_score);

-- Composite index for dashboard default sort: -is_it_priority, -priority_score, -scraped_at
CREATE INDEX IF NOT EXISTS idx_tender_dashboard_sort
    ON web_tenderresult (is_it_priority DESC, priority_score DESC, scraped_at DESC);

-- Composite index for HPS range filtering
CREATE INDEX IF NOT EXISTS idx_tender_hps
    ON web_tenderresult (hps);

COMMENT ON TABLE  web_tenderresult                IS 'Scraped tender packages from SPSE Inaproc';
COMMENT ON COLUMN web_tenderresult.kode_instansi  IS 'SPSE instansi code, e.g. jakarta, kemendagri';
COMMENT ON COLUMN web_tenderresult.id_lelang      IS 'Unique tender package ID from SPSE';
COMMENT ON COLUMN web_tenderresult.hps            IS 'HPS value in Rupiah (BigInt)';
COMMENT ON COLUMN web_tenderresult.kbli_code      IS 'Extracted KBLI code (5 digits)';
COMMENT ON COLUMN web_tenderresult.is_it_priority IS 'Auto-tagged IT priority by scorer engine';
COMMENT ON COLUMN web_tenderresult.priority_score IS '0=non-IT, 80=keyword-match, 100=KBLI-match';

-- ============================================================
-- 3. web_crawljob
--    Tracks crawl job execution status.
-- ============================================================
CREATE TABLE IF NOT EXISTS web_crawljob (
    id              BIGSERIAL       PRIMARY KEY,
    instansi_input  TEXT            NOT NULL,
    workers         INTEGER         NOT NULL DEFAULT 3,
    status          VARCHAR(20)     NOT NULL DEFAULT 'pending',
    total_packages  INTEGER         NOT NULL DEFAULT 0,
    total_details   INTEGER         NOT NULL DEFAULT 0,
    error_message   TEXT            NOT NULL DEFAULT '',
    started_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ     NULL
);

CREATE INDEX IF NOT EXISTS idx_crawljob_status
    ON web_crawljob (status);

COMMENT ON TABLE  web_crawljob              IS 'Crawl job execution log';
COMMENT ON COLUMN web_crawljob.status       IS 'pending | running | completed | failed';
COMMENT ON COLUMN web_crawljob.instansi_input IS 'Comma-separated codes or "all"';

-- ============================================================
-- 4. Django session table (required by django.contrib.sessions)
-- ============================================================
CREATE TABLE IF NOT EXISTS django_session (
    session_key   VARCHAR(40)   PRIMARY KEY,
    session_data  TEXT          NOT NULL,
    expire_date   TIMESTAMPTZ   NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_expire
    ON django_session (expire_date);

-- ============================================================
-- 5. Django admin / auth tables (minimal — for login to work)
--    If you use `manage.py migrate`, Django creates these
--    automatically. Included here for manual `psql` setup.
-- ============================================================
CREATE TABLE IF NOT EXISTS auth_user (
    id              SERIAL        PRIMARY KEY,
    password        VARCHAR(128)  NOT NULL,
    last_login      TIMESTAMPTZ   NULL,
    is_superuser    BOOLEAN       NOT NULL DEFAULT FALSE,
    username        VARCHAR(150)  NOT NULL UNIQUE,
    first_name      VARCHAR(150)  NOT NULL DEFAULT '',
    last_name       VARCHAR(150)  NOT NULL DEFAULT '',
    email           VARCHAR(254)  NOT NULL DEFAULT '',
    is_staff        BOOLEAN       NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN       NOT NULL DEFAULT TRUE,
    date_joined     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auth_group (
    id    SERIAL       PRIMARY KEY,
    name  VARCHAR(150) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS auth_permission (
    id              SERIAL        PRIMARY KEY,
    name            VARCHAR(255)  NOT NULL,
    content_type_id INTEGER       NOT NULL,
    codename        VARCHAR(100)  NOT NULL,
    UNIQUE (content_type_id, codename)
);

CREATE TABLE IF NOT EXISTS django_content_type (
    id        SERIAL       PRIMARY KEY,
    app_label VARCHAR(100) NOT NULL,
    model     VARCHAR(100) NOT NULL,
    UNIQUE (app_label, model)
);

CREATE TABLE IF NOT EXISTS django_migrations (
    id      SERIAL       PRIMARY KEY,
    app     VARCHAR(255) NOT NULL,
    name    VARCHAR(255) NOT NULL,
    applied TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Done. Run seed.sql next for initial KBLI data.
-- ============================================================

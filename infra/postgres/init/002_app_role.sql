-- The role the APPLICATION connects as. Deliberately not the owner of anything.
--
-- ADR-0004 makes table privileges the primary append-only control: a role without
-- UPDATE cannot issue one, and cannot disable a trigger on a table it does not own.
-- That only means something if the application is not the owner, hence this role.
--
-- Alembic continues to connect as `naddp` (the owner) to run migrations. The split is
-- the point: schema change is a privileged, deliberate act; serving traffic is not.
--
-- The migration grants SELECT/INSERT and revokes UPDATE/DELETE/TRUNCATE on the
-- append-only tables, guarded on this role existing -- so a managed Postgres that
-- forbids CREATE ROLE still migrates cleanly, just without this layer.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'naddp_app') THEN
        CREATE ROLE naddp_app LOGIN PASSWORD 'naddp_app' NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE naddp TO naddp_app;
GRANT USAGE ON SCHEMA public TO naddp_app;

-- Applies to tables the migration has not created yet, so the app role picks up
-- sensible defaults automatically. The migration then narrows the two append-only
-- tables back down.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO naddp_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO naddp_app;

-- Database initialization script for PostgreSQL
-- This script is run by docker-compose during initial database setup

-- Create extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- For text search

-- Create custom types
DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('user', 'admin', 'developer');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Grant necessary permissions
GRANT ALL PRIVILEGES ON DATABASE jingxin TO jingxin_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO jingxin_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO jingxin_user;

-- Create indexes for common queries (will be created by Alembic, but good to have here for reference)
-- These are examples and should match your actual schema

-- Enable row-level security (optional, for future use)
-- ALTER TABLE users_shadow ENABLE ROW LEVEL SECURITY;

-- Create audit trigger function (optional enhancement)
CREATE OR REPLACE FUNCTION audit_trigger_func()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Database initialization completed successfully';
END $$;

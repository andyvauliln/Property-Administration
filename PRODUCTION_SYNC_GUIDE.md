# Production Database Sync Guide

## Overview
The `start.sh` script now automatically syncs your local database with the production database every time you start the application. This ensures you're always working with the latest production data during development.

## Configuration

### 1. Set Up Your .env File
Make sure your `.env` file contains the production database password:

```bash
# Database settings
PGDATABASE=railway
PGUSER=postgres_user
PGPASSWORD=eEdjNBNeHtIvzO5RnzDJ  # Production database password
PGHOST=localhost
PGPORT=5432
```

### 2. Production Server Details (Already Configured)
- **Production Host:** 68.183.124.79
- **Database Name:** railway
- **User:** postgres_user

## Usage

### Start Application with Production Sync (Default)
```bash
./start.sh
```
This will:
1. Start PostgreSQL if needed
2. Activate virtual environment
3. **Dump production database from 68.183.124.79**
4. **Restore it to your local database**
5. Run migrations
6. Start Tailwind CSS
7. Start Django server

### Start Without Production Sync
If you don't want to sync the database this time:
```bash
./start.sh --no-sync
```

### Sync Database Only (Without Starting Server)
To only sync the database without starting the application:
```bash
./start.sh --sync-only
```

### Other Options
```bash
./start.sh -y              # Auto-kill process on port without asking
./start.sh -p 8080         # Start on custom port
./start.sh --no-sync -y    # Start without sync, auto-kill port conflicts
```

## Database Backups

All production database dumps are automatically saved to:
```
./db_backups/production_backup_YYYYMMDD_HHMMSS.sql
```

The script automatically keeps only the **last 5 backups** to save disk space.

### Manual Restore from Backup
If you need to restore from a specific backup:
```bash
psql -U postgres_user -d railway < db_backups/production_backup_20231127_143022.sql
```

## Troubleshooting

### "Failed to dump production database"
**Solution:** Make sure `PGPASSWORD` is set correctly in your `.env` file.

```bash
# Check if password is set
echo $PGPASSWORD

# If not set, reload your .env
source .env
```

### Connection Timeout
**Solution:** Verify you can reach the production server:
```bash
pg_isready -h 68.183.124.79 -U postgres_user -d railway
```

### Permission Denied
**Solution:** Ensure your IP is whitelisted on the production PostgreSQL server.

### Want to Skip Sync Prompt
If the sync fails and you want to continue anyway, you'll be prompted:
```
⚠ Failed to sync production database
Continue without syncing? [Y/n]:
```
- Press `Y` or `Enter` to continue
- Press `N` to abort

## Security Notes

⚠️ **Important Security Considerations:**
1. Never commit your `.env` file (already in `.gitignore`)
2. Keep your production database password secure
3. Production data may contain sensitive information
4. Backups in `db_backups/` are excluded from git

## Advanced Configuration

Edit `start.sh` to change these defaults:
```bash
SYNC_PRODUCTION=true          # Set to false to disable by default
PRODUCTION_HOST="68.183.124.79"
PRODUCTION_USER="postgres_user"
PRODUCTION_DB="railway"
LOCAL_DB="railway"
LOCAL_USER="postgres_user"
```

## Manual Database Operations

### Manual Production Dump
```bash
pg_dump -h 68.183.124.79 -U postgres_user -d railway > manual_backup.sql
```

### Manual Local Restore
```bash
psql -U postgres_user -d railway < manual_backup.sql
```

### Dump Specific Tables
```bash
pg_dump -h 68.183.124.79 -U postgres_user -d railway \
  --table=mysite_apartment \
  --table=mysite_booking \
  --table=mysite_payment \
  > specific_tables_backup.sql
```


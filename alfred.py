import os
import sys
import sqlite3
import time
import argparse
import traceback
from loguru import logger
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from fnmatch import fnmatch
import threading

# Get directories from environment variables
symlink_directories = os.getenv('SYMLINK_DIR')
torrents_directories = os.getenv('TORRENTS_DIR')
delete_behavior = os.getenv('DELETE_BEHAVIOR', 'files').lower()
scan_interval = int(os.getenv('SCAN_INTERVAL', '720'))  # Default to 12 hours if not set
PENDING_DELETION_GRACE_SECONDS = int(os.getenv('PENDING_DELETION_GRACE_SECONDS', '60'))
DRY_RUN = os.getenv('DRY_RUN', 'false').lower() == 'true'
RUN_ON_STARTUP = os.getenv('RUN_ON_STARTUP', 'true').lower() == 'true'

if not symlink_directories or not torrents_directories:
    logger.error("Required environment variables SYMLINK_DIR and TORRENTS_DIR must be set")
    sys.exit(1)

if delete_behavior not in ['files', 'folders']:
    logger.error("DELETE_BEHAVIOR must be either 'files' or 'folders'")
    sys.exit(1)

if scan_interval < 0:
    logger.error("SCAN_INTERVAL must be a positive number or 0 to disable")
    sys.exit(1)

# Convert directories to lists and clean up paths
symlink_directories = [path.strip() for path in symlink_directories.split(',') if path.strip()]
torrents_directories = [path.strip() for path in torrents_directories.split(',') if path.strip()]

# Set up the SQLite database file
db_file = '/app/data/symlinks.db'

# Customize Loguru logger
logger.remove()  # Remove default logger
logger.add(
    sys.stdout,  # Print logs to the console
    format="<black>{time:YYYY-MM-DD HH:mm:ss}</black> | <level>{level}</level> | <yellow>{message}</yellow>",
    colorize=True,
)

def get_db_connection():
    conn = sqlite3.connect(db_file, timeout=30)  # 30 second timeout
    # Enable WAL mode for better concurrency
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=30000')  # 30 second busy timeout
    return conn

def execute_with_retry(cursor, query, params=None, max_retries=3):
    """Execute a query with retry logic for database locks"""
    for attempt in range(max_retries):
        try:
            if params:
                return cursor.execute(query, params)
            else:
                return cursor.execute(query)
        except sqlite3.OperationalError as e:
            if 'database is locked' in str(e) and attempt < max_retries - 1:
                time.sleep(1)  # Wait 1 second before retrying
                continue
            raise

def create_table(conn):
    cursor = conn.cursor()
    execute_with_retry(cursor, '''
    CREATE TABLE IF NOT EXISTS symlinks (
        id INTEGER PRIMARY KEY,
        symlink TEXT UNIQUE,
        target TEXT,
        ref_count INTEGER DEFAULT 1
    )
    ''')
    execute_with_retry(cursor, '''
    CREATE TABLE IF NOT EXISTS scan_times (
        id INTEGER PRIMARY KEY,
        last_scan_time INTEGER,
        scan_interval INTEGER
    )
    ''')
    execute_with_retry(cursor, '''
    CREATE TABLE IF NOT EXISTS deletions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symlink TEXT NOT NULL,
        target TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        reason TEXT
    )
    ''')
    execute_with_retry(cursor, '''
    CREATE TABLE IF NOT EXISTS metrics_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        total_symlinks INTEGER,
        unique_targets INTEGER,
        total_deletions INTEGER
    )
    ''')
    execute_with_retry(cursor, '''
    CREATE TABLE IF NOT EXISTS scan_statistics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_time INTEGER NOT NULL,
        files_checked INTEGER DEFAULT 0,
        files_deleted INTEGER DEFAULT 0,
        folders_deleted INTEGER DEFAULT 0
    )
    ''')
    execute_with_retry(cursor, '''
    CREATE TABLE IF NOT EXISTS pending_deletions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target TEXT NOT NULL,
        scheduled_time INTEGER NOT NULL
    )
    ''')
    conn.commit()

def upsert_symlink(file_path, conn=None):
    if os.path.islink(file_path):
        target = os.readlink(file_path)
        if not os.path.exists(target):
            logger.warning(f"⚠️ Target {target} does not exist for symlink {file_path}")
            return
        try:
            should_close = False
            if conn is None:
                conn = get_db_connection()
                should_close = True
            
            cursor = conn.cursor()
            # Check if the symlink already exists
            execute_with_retry(cursor, 'SELECT target FROM symlinks WHERE symlink = ?', (file_path,))
            symlink_row = cursor.fetchone()
            if symlink_row:
                # If the symlink exists but points to a different target, we need to handle that
                old_target = symlink_row[0]
                if old_target != target:
                    # Decrement ref_count for old target
                    execute_with_retry(cursor, 'SELECT ref_count FROM symlinks WHERE target = ?', (old_target,))
                    old_ref_count = cursor.fetchone()[0]
                    if old_ref_count > 1:
                        execute_with_retry(cursor, 'UPDATE symlinks SET ref_count = ? WHERE target = ?', (old_ref_count - 1, old_target))
                    else:
                        # If this was the last reference to the old target, delete it
                        if os.path.exists(old_target):
                            if delete_behavior == 'files':
                                os.remove(old_target)
                                logger.info(f"❌ Deleted old target file: {old_target}")
                            else:
                                parent_dir = os.path.dirname(old_target)
                                if os.path.exists(parent_dir):
                                    import shutil
                                    shutil.rmtree(parent_dir)
                                    logger.info(f"❌ Deleted old target folder: {parent_dir}")
                        execute_with_retry(cursor, 'DELETE FROM symlinks WHERE target = ?', (old_target,))
                    
                    # Update the symlink to point to the new target
                    execute_with_retry(cursor, 'UPDATE symlinks SET target = ? WHERE symlink = ?', (target, file_path))
                else:
                    logger.info(f"🔗 Symlink {file_path} already exists in the database with the same target.")
            else:
                # Check if the target exists
                execute_with_retry(cursor, 'SELECT ref_count FROM symlinks WHERE target = ?', (target,))
                target_row = cursor.fetchone()
                if target_row:
                    ref_count = target_row[0] + 1
                    execute_with_retry(cursor, 'UPDATE symlinks SET ref_count = ? WHERE target = ?', (ref_count, target))
                    execute_with_retry(cursor, 'INSERT INTO symlinks (symlink, target, ref_count) VALUES (?, ?, ?)', (file_path, target, ref_count))
                    logger.info(f"🔄 Incremented ref_count for target {target}, new ref_count is {ref_count}")
                else:
                    ref_count = 1
                    execute_with_retry(cursor, 'INSERT INTO symlinks (symlink, target, ref_count) VALUES (?, ?, ?)', (file_path, target, ref_count))
                    logger.info(f"🆕 Created new target entry with ref_count {ref_count}")
            
            if should_close:
                conn.commit()
                conn.close()
            
            logger.info(f"🔗 Added/Updated symlink: {file_path} -> {target}")
        except Exception as e:
            logger.error(f"Error updating symlink {file_path}: {e}")
            logger.debug(traceback.format_exc())
            if should_close:
                conn.close()

def find_non_linked_files(torrents_directories, symlink_directories, dry_run=False, no_confirm=False, exclude_patterns=[]):
    dst_links = set()
    # First, scan all symlinks and add them to the database
    logger.info("🔍 Scanning for existing symlinks...")
    
    # Open a single database connection for batch operations
    with get_db_connection() as conn:
        # Enable WAL mode for better performance
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA cache_size=10000')
        
        # Start transaction
        conn.execute('BEGIN TRANSACTION')
        
        try:
            for symlink_directory in symlink_directories:
                if not os.path.exists(symlink_directory):
                    logger.warning(f"⚠️ Symlink directory {symlink_directory} does not exist or is not accessible.")
                    continue
                for root, _, files in os.walk(symlink_directory):
                    for entry in files:
                        dst_path = os.path.join(root, entry)
                        if os.path.islink(dst_path):
                            dst_links.add(os.path.realpath(dst_path))
                            upsert_symlink(dst_path, conn)
            
            # Commit the transaction
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Error during batch symlink processing: {e}")
            raise

    used_files = set()
    all_files = set()

    for torrents_directory in torrents_directories:
        if not os.path.exists(torrents_directory):
            logger.warning(f"⚠️ Directory {torrents_directory} does not exist or is not accessible.")
            continue

        for root, _, files in os.walk(torrents_directory):
            # Skip excluded patterns
            if any(fnmatch(root, pattern) for pattern in exclude_patterns):
                continue

            for file in files:
                file_path = os.path.join(root, file)
                all_files.add(file_path)

                if any(fnmatch(file, pattern) for pattern in exclude_patterns):
                    continue

                src_file = os.path.realpath(file_path)
                if src_file in dst_links:
                    used_files.add(file_path)
                    continue  # This file is used, move to the next file

    unused_files = all_files - used_files

    total_files = len(all_files)
    deleted_files = 0
    deleted_folders = 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        current_time = int(time.time())

        for file_path in unused_files:
            if not os.path.exists(file_path):
                continue  # Skip if the file doesn't exist

            if os.path.isfile(file_path):
                logger.info(f"File {file_path} is not used!")

                if dry_run:
                    if delete_behavior == 'files':
                        logger.info(f"🟠 Dry-run: Would delete file: {file_path}")
                    else:
                        parent_dir = os.path.dirname(file_path)
                        logger.info(f"🟠 Dry-run: Would delete parent folder: {parent_dir}")
                else:
                    if no_confirm:
                        response = 'y'
                    else:
                        if delete_behavior == 'files':
                            response = input(f"Do you want to delete this file '{file_path}'? (y/n): ")
                        else:
                            parent_dir = os.path.dirname(file_path)
                            response = input(f"Do you want to delete the parent folder '{parent_dir}'? (y/n): ")
                    if response.lower() == 'y':
                        try:
                            if delete_behavior == 'files':
                                os.remove(file_path)
                                logger.info(f"File {file_path} deleted!")
                                # Record deletion
                                execute_with_retry(cursor, '''
                                    INSERT INTO deletions (symlink, target, timestamp, reason)
                                    VALUES (?, ?, ?, ?)
                                ''', (file_path, file_path, current_time, 'unused_file'))
                                deleted_files += 1
                            else:
                                parent_dir = os.path.dirname(file_path)
                                import shutil
                                shutil.rmtree(parent_dir)
                                logger.info(f"Parent folder {parent_dir} deleted!")
                                # Record deletion
                                execute_with_retry(cursor, '''
                                    INSERT INTO deletions (symlink, target, timestamp, reason)
                                    VALUES (?, ?, ?, ?)
                                ''', (parent_dir, file_path, current_time, 'unused_folder'))
                                deleted_folders += 1
                        except Exception as e:
                            logger.error(f"Error deleting {'file' if delete_behavior == 'files' else 'parent folder'}: {e}")
                            logger.error(traceback.format_exc())
                    else:
                        logger.info(f"{'File' if delete_behavior == 'files' else 'Parent folder'} not deleted!")
            else:
                # Skip directories or other non-file paths
                continue

        # Record scan statistics
        execute_with_retry(cursor, '''
            INSERT INTO scan_statistics (scan_time, files_checked, files_deleted, folders_deleted)
            VALUES (?, ?, ?, ?)
        ''', (current_time, total_files, deleted_files, deleted_folders))
        conn.commit()

    logger.info(f"Total files checked: {total_files}")
    logger.info(f"Total {'files' if delete_behavior == 'files' else 'parent folders'} deleted: {deleted_files if delete_behavior == 'files' else deleted_folders}")
    
    # Record metrics after scan
    with get_db_connection() as conn:
        update_metrics_if_needed(conn)

def delete_missing_target(symlink, dry_run):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # First get the target and check remaining references
        execute_with_retry(cursor, 'SELECT target FROM symlinks WHERE symlink = ?', (symlink,))
        row = cursor.fetchone()
        if row:
            target = row[0]
            logger.info(f"🔍 Found target {target} for symlink {symlink}")
            # Check how many symlinks point to this target
            execute_with_retry(cursor, 'SELECT COUNT(*) FROM symlinks WHERE target = ?', (target,))
            total_refs = cursor.fetchone()[0]
            logger.info(f"🔍 Target {target} has {total_refs} references")
            if total_refs == 1:
                # This is the last reference, schedule for deletion
                scheduled_time = int(time.time()) + PENDING_DELETION_GRACE_SECONDS
                execute_with_retry(cursor, '''
                    INSERT INTO pending_deletions (target, scheduled_time)
                    VALUES (?, ?)
                ''', (target, scheduled_time))
                logger.info(f"⏳ Scheduled target {target} for deletion in {PENDING_DELETION_GRACE_SECONDS} seconds")
                # Remove all entries for this target from the database
                execute_with_retry(cursor, 'DELETE FROM symlinks WHERE target = ?', (target,))
                logger.info(f"❌ Removed all database entries for target: {target}")
            else:
                # Just remove this symlink entry
                execute_with_retry(cursor, 'DELETE FROM symlinks WHERE symlink = ?', (symlink,))
                logger.info(f"🔄 Target {target} still has {total_refs - 1} references")
            conn.commit()
        else:
            logger.warning(f"⚠️ No database entry found for symlink {symlink}")
            # If symlink not found in database, try to get its target and delete it
            try:
                if os.path.islink(symlink):
                    target = os.readlink(symlink)
                    scheduled_time = int(time.time()) + PENDING_DELETION_GRACE_SECONDS
                    execute_with_retry(cursor, '''
                        INSERT INTO pending_deletions (target, scheduled_time)
                        VALUES (?, ?)
                    ''', (target, scheduled_time))
                    logger.info(f"⏳ Scheduled target {target} for deletion in {PENDING_DELETION_GRACE_SECONDS} seconds (no DB entry)")
            except Exception as e:
                logger.error(f"Error handling symlink {symlink}: {e}")
                logger.error(traceback.format_exc())

# Event handler for file system events
class SymlinkEventHandler(FileSystemEventHandler):
    def __init__(self, dry_run):
        self.dry_run = dry_run
        logger.info("🔍 Real-time symlink monitoring initialized")

    def on_created(self, event):
        try:
            logger.debug(f"📝 Detected creation event: {event.src_path}")
            if os.path.islink(event.src_path):
                logger.info(f"🔗 New symlink created: {event.src_path}")
                with get_db_connection() as conn:
                    upsert_symlink(event.src_path, conn)
                    update_metrics_if_needed(conn)
        except Exception as e:
            logger.error(f"Error handling creation of {event.src_path}: {e}")
            logger.debug(traceback.format_exc())

    def on_modified(self, event):
        try:
            logger.debug(f"📝 Detected modification event: {event.src_path}")
            if os.path.islink(event.src_path):
                logger.info(f"🔗 Symlink modified: {event.src_path}")
                upsert_symlink(event.src_path)
        except Exception as e:
            logger.error(f"Error handling modification of {event.src_path}: {e}")
            logger.debug(traceback.format_exc())

    def on_deleted(self, event):
        try:
            logger.debug(f"📝 Detected deletion event: {event.src_path}")
            with get_db_connection() as conn:
                cursor = conn.cursor()
                execute_with_retry(cursor, 'SELECT target, ref_count FROM symlinks WHERE symlink = ?', (event.src_path,))
                row = cursor.fetchone()
                if row:
                    target, ref_count = row
                    ref_count -= 1
                    if ref_count <= 0:
                        # Schedule for deletion instead of deleting immediately
                        scheduled_time = int(time.time()) + PENDING_DELETION_GRACE_SECONDS
                        execute_with_retry(cursor, '''
                            INSERT INTO pending_deletions (target, scheduled_time)
                            VALUES (?, ?)
                        ''', (target, scheduled_time))
                        logger.info(f"⏳ Scheduled target {target} for deletion in {PENDING_DELETION_GRACE_SECONDS} seconds (event handler)")
                        execute_with_retry(cursor, 'DELETE FROM symlinks WHERE target = ?', (target,))
                        execute_with_retry(cursor, '''
                            INSERT INTO deletions (symlink, target, timestamp, reason)
                            VALUES (?, ?, ?, ?)
                        ''', (event.src_path, target, int(time.time()), 'last_reference_deleted'))
                        logger.info(f"❌ Removed symlink entry from database: {event.src_path} (was pointing to {target})")
                    else:
                        execute_with_retry(cursor, 'UPDATE symlinks SET ref_count = ? WHERE target = ?', (ref_count, target))
                        execute_with_retry(cursor, 'DELETE FROM symlinks WHERE symlink = ?', (event.src_path,))
                        execute_with_retry(cursor, '''
                            INSERT INTO deletions (symlink, target, timestamp, reason)
                            VALUES (?, ?, ?, ?)
                        ''', (event.src_path, target, int(time.time()), 'symlink_deleted'))
                        logger.info(f"🔄 Decremented ref_count for target {target}, new ref_count is {ref_count}")
                    conn.commit()
                    update_metrics_if_needed(conn)
                else:
                    logger.debug(f"⚠️ Symlink {event.src_path} not found in the database.")
        except Exception as e:
            logger.error(f"Error handling deletion of {event.src_path}: {e}")
            logger.debug(traceback.format_exc())

    def on_moved(self, event):
        try:
            logger.debug(f"\U0001F4DD Detected move event: {event.src_path} -> {event.dest_path}")
            conn_used = False
            # First handle the new location
            if os.path.islink(event.dest_path):
                logger.info(f"\U0001F517 Symlink moved to: {event.dest_path}")
                target = os.readlink(event.dest_path)
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    execute_with_retry(cursor, 'SELECT ref_count FROM symlinks WHERE target = ?', (target,))
                    target_row = cursor.fetchone()
                    if target_row:
                        ref_count = target_row[0] + 1
                        execute_with_retry(cursor, 'UPDATE symlinks SET ref_count = ? WHERE target = ?', (ref_count, target))
                        execute_with_retry(cursor, 'INSERT INTO symlinks (symlink, target, ref_count) VALUES (?, ?, ?)', 
                                         (event.dest_path, target, ref_count))
                        logger.info(f"\U0001F501 Updated ref_count for target {target}, new ref_count is {ref_count}")
                    else:
                        upsert_symlink(event.dest_path)
                    conn.commit()
                    with get_db_connection() as conn:
                        update_metrics_if_needed(conn)
                    conn_used = True
            # Then handle the old location
            if os.path.exists(event.src_path) and os.path.islink(event.src_path):
                logger.info(f"\U0001F517 Cleaning up old symlink: {event.src_path}")
                target = os.readlink(event.src_path)
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    execute_with_retry(cursor, 'SELECT ref_count FROM symlinks WHERE target = ?', (target,))
                    target_row = cursor.fetchone()
                    if target_row:
                        ref_count = target_row[0] - 1
                        if ref_count <= 0:
                            scheduled_time = int(time.time()) + PENDING_DELETION_GRACE_SECONDS
                            execute_with_retry(cursor, '''
                                INSERT INTO pending_deletions (target, scheduled_time)
                                VALUES (?, ?)
                            ''', (target, scheduled_time))
                            logger.info(f"\u23F3 Scheduled target {target} for deletion in {PENDING_DELETION_GRACE_SECONDS} seconds (move handler)")
                            execute_with_retry(cursor, 'DELETE FROM symlinks WHERE target = ?', (target,))
                            logger.info(f"\u274C Removed symlink entry from database: {event.src_path} (was pointing to {target})")
                        else:
                            execute_with_retry(cursor, 'UPDATE symlinks SET ref_count = ? WHERE target = ?', (ref_count, target))
                            execute_with_retry(cursor, 'DELETE FROM symlinks WHERE symlink = ?', (event.src_path,))
                            logger.info(f"\U0001F501 Decremented ref_count for target {target}, new ref_count is {ref_count}")
                    conn.commit()
                    with get_db_connection() as conn:
                        update_metrics_if_needed(conn)
                    conn_used = True
            if not conn_used:
                with get_db_connection() as conn:
                    update_metrics_if_needed(conn)
        except Exception as e:
            logger.error(f"Error handling movement from {event.src_path} to {event.dest_path}: {e}")
            logger.debug(traceback.format_exc())

def has_children(directory):
    """Check if a directory has any children (files or subdirectories)."""
    try:
        return any(os.scandir(directory))
    except Exception as e:
        logger.error(f"Error scanning directory {directory}: {e}")
        return False

def wait_for_children(directories):
    """Wait until all directories have children."""
    while True:
        all_have_children = True
        for directory in directories:
            if not has_children(directory):
                logger.info(f"⚠️ Directory {directory} is empty. Waiting for content...")
                all_have_children = False
                break
        
        if all_have_children:
            logger.info("✅ All directories have content. Proceeding...")
            return True
        
        time.sleep(60)  # Wait 1 minute before checking again

def get_last_scan_time(conn):
    cursor = conn.cursor()
    cursor.execute('SELECT last_scan_time, scan_interval FROM scan_times ORDER BY id DESC LIMIT 1')
    result = cursor.fetchone()
    return result if result else (None, None)

def update_scan_time(conn, scan_interval):
    cursor = conn.cursor()
    current_time = int(time.time())
    cursor.execute('INSERT INTO scan_times (last_scan_time, scan_interval) VALUES (?, ?)', 
                  (current_time, scan_interval))
    conn.commit()

def should_perform_scan(conn, scan_interval):
    if scan_interval == 0:
        return True  # Always scan if interval is 0
    
    last_scan_time, last_interval = get_last_scan_time(conn)
    if not last_scan_time:
        return True  # No previous scan, perform initial scan
    
    current_time = int(time.time())
    time_since_last_scan = current_time - last_scan_time
    return time_since_last_scan >= (scan_interval * 60)  # Convert minutes to seconds

def background_scan(dry_run, no_confirm, exclude_patterns):
    """Run find_non_linked_files in the background at specified intervals."""
    while True:
        try:
            with get_db_connection() as conn:
                if should_perform_scan(conn, scan_interval):
                    logger.info("🔄 Starting background scan...")
                    find_non_linked_files(
                        torrents_directories,
                        symlink_directories,
                        dry_run,
                        no_confirm,
                        exclude_patterns
                    )
                    update_scan_time(conn, scan_interval)
            
            time.sleep(60)  # Check every minute if it's time to scan
        except Exception as e:
            logger.error(f"Error during background scan: {e}")
            logger.error(traceback.format_exc())
            time.sleep(60)  # Wait 1 minute before retrying on error

def record_metrics(conn=None):
    """Record current metrics to the history table"""
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True
    try:
        cursor = conn.cursor()
        # Get current stats
        execute_with_retry(cursor, 'SELECT COUNT(*) as total, COUNT(DISTINCT target) as unique_targets FROM symlinks')
        total, unique = cursor.fetchone()
        
        # Get total deletions
        execute_with_retry(cursor, 'SELECT COUNT(*) FROM deletions')
        deletions = cursor.fetchone()[0]
        
        # Record metrics
        execute_with_retry(cursor, '''
        INSERT INTO metrics_history (total_symlinks, unique_targets, total_deletions)
        VALUES (?, ?, ?)
        ''', (total, unique, deletions))
        conn.commit()
    finally:
        if should_close:
            conn.close()

# Update metrics after significant operations
def update_metrics_if_needed(conn):
    """Update metrics if significant changes occurred"""
    try:
        record_metrics(conn)
    except Exception as e:
        logger.error(f"Error recording metrics: {e}")
        logger.debug(traceback.format_exc())

def reload_env_settings():
    """Reload environment variables and reinitialize components."""
    global symlink_directories, torrents_directories, delete_behavior, scan_interval
    global PENDING_DELETION_GRACE_SECONDS, DRY_RUN, RUN_ON_STARTUP

    try:
        # Read and parse the .env file
        env_file = '/app/data/.env'
        if not os.path.exists(env_file):
            raise FileNotFoundError(f"Environment file not found: {env_file}")

        with open(env_file, 'r') as f:
            env_lines = f.readlines()

        # Parse environment variables
        env_vars = {}
        for line in env_lines:
            line = line.strip()
            if line and not line.startswith('#'):
                key, value = line.split('=', 1)
                env_vars[key.strip()] = value.strip()

        # Update environment variables
        for key, value in env_vars.items():
            os.environ[key] = value

        # Reload global variables
        symlink_directories = os.getenv('SYMLINK_DIR', '').split(',')
        torrents_directories = os.getenv('TORRENTS_DIR', '').split(',')
        delete_behavior = os.getenv('DELETE_BEHAVIOR', 'files').lower()
        scan_interval = int(os.getenv('SCAN_INTERVAL', '720'))
        PENDING_DELETION_GRACE_SECONDS = int(os.getenv('PENDING_DELETION_GRACE_SECONDS', '60'))
        DRY_RUN = os.getenv('DRY_RUN', 'false').lower() == 'true'
        RUN_ON_STARTUP = os.getenv('RUN_ON_STARTUP', 'true').lower() == 'true'

        # Clean up and validate directories
        symlink_directories = [path.strip() for path in symlink_directories if path.strip()]
        torrents_directories = [path.strip() for path in torrents_directories if path.strip()]

        # Validate settings
        if not symlink_directories or not torrents_directories:
            raise ValueError("Required environment variables SYMLINK_DIR and TORRENTS_DIR must be set")

        if delete_behavior not in ['files', 'folders']:
            raise ValueError("DELETE_BEHAVIOR must be either 'files' or 'folders'")

        if scan_interval < 0:
            raise ValueError("SCAN_INTERVAL must be a positive number or 0")

        # Update scan times table with new interval
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Get the last scan time
            cursor.execute('SELECT last_scan_time FROM scan_times ORDER BY id DESC LIMIT 1')
            row = cursor.fetchone()
            if row:
                last_scan_time = row[0]
                # Insert new record with updated interval
                cursor.execute('INSERT INTO scan_times (last_scan_time, scan_interval) VALUES (?, ?)',
                             (last_scan_time, scan_interval))
                conn.commit()

        # Log the reloaded settings
        logger.info("🔄 Settings reloaded successfully")
        logger.info(f"📁 Symlink directories: {', '.join(symlink_directories)}")
        logger.info(f"📁 Torrent directories: {', '.join(torrents_directories)}")
        logger.info(f"🗑️ Delete behavior: {delete_behavior}")
        logger.info(f"⏱️ Scan interval: {scan_interval} minutes")
        logger.info(f"🕒 Pending deletion grace period: {PENDING_DELETION_GRACE_SECONDS} seconds")
        logger.info(f"🧪 Dry run mode: {DRY_RUN}")
        logger.info(f"🚦 Run on startup: {RUN_ON_STARTUP}")

        return True

    except Exception as e:
        logger.error(f"Error reloading settings: {e}")
        logger.debug(traceback.format_exc())
        raise

def clear_pending_deletions_on_startup():
    """Clear all pending deletions on startup to prevent deletion of files during restart"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        execute_with_retry(cursor, 'SELECT COUNT(*) FROM pending_deletions')
        count = cursor.fetchone()[0]
        if count > 0:
            execute_with_retry(cursor, 'DELETE FROM pending_deletions')
            conn.commit()
            logger.info(f"🧹 Cleared {count} pending deletions on startup")

def main(dry_run, no_confirm, exclude_patterns):
    # Validate paths and permissions
    for symlink_directory in symlink_directories:
        if not os.path.exists(symlink_directory):
            logger.error(f"Symlink directory {symlink_directory} does not exist.")
            sys.exit(1)
        if not os.access(symlink_directory, os.W_OK):
            logger.error(f"No write permission for {symlink_directory}")
            sys.exit(1)
    for torrents_directory in torrents_directories:
        if not os.path.exists(torrents_directory):
            logger.error(f"Torrents directory {torrents_directory} does not exist.")
            sys.exit(1)
        if not os.access(torrents_directory, os.W_OK):
            logger.error(f"No write permission for {torrents_directory}")
            sys.exit(1)

    # Validate DELETE_BEHAVIOR
    if delete_behavior not in ['files', 'folders']:
        logger.error("DELETE_BEHAVIOR must be either 'files' or 'folders'")
        sys.exit(1)

    logger.info("🔄 Initializing database...")
    with get_db_connection() as conn:
        create_table(conn)
        record_metrics(conn)  # Record initial metrics
        
        # Clear pending deletions on startup
        clear_pending_deletions_on_startup()
        
        # Check if we need to perform a full scan
        if should_perform_scan(conn, scan_interval):
            logger.info("⏳ Waiting for content in torrents directories...")
            wait_for_children(torrents_directories)

            logger.info(f"🟢 Running startup script with DELETE_BEHAVIOR={delete_behavior}...")
            find_non_linked_files(
                torrents_directories,
                symlink_directories,
                dry_run,
                no_confirm,
                exclude_patterns
            )
            update_scan_time(conn, scan_interval)
            update_metrics_if_needed(conn)  # Record metrics after scan
        else:
            last_scan_time, _ = get_last_scan_time(conn)
            next_scan = last_scan_time + (scan_interval * 60)
            time_until_next = next_scan - int(time.time())
            logger.info(f"⏳ Skipping initial scan. Next scan in {time_until_next//60} minutes")

    # Start the cleanup thread AFTER the tables are created
    if RUN_ON_STARTUP:
        cleanup_thread = threading.Thread(target=start_pending_deletion_cleanup, daemon=True)
        cleanup_thread.start()

    logger.info("")
    logger.info("🔍 Setting up real-time monitoring...")
    logger.info(f"🔍 Monitoring symlink directories: {', '.join(symlink_directories)}")
    logger.info(f"📁 Monitoring torrent directories: {', '.join(torrents_directories)}")
    logger.info("")

    # Start background scan if interval is set
    if scan_interval > 0:
        background_thread = threading.Thread(
            target=background_scan,
            args=(dry_run, no_confirm, exclude_patterns),
            daemon=True
        )
        background_thread.start()
        logger.info(f"🔄 Background scanning started with interval of {scan_interval} minutes")

    event_handler = SymlinkEventHandler(dry_run)
    observer = Observer()
    for symlink_directory in symlink_directories:
        observer.schedule(event_handler, path=symlink_directory, recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        logger.info("🛑 Real-time scanning stopped by user.")
    observer.join()

def cleanup_pending_deletions():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        now = int(time.time())
        execute_with_retry(cursor, 'SELECT id, target FROM pending_deletions WHERE scheduled_time <= ?', (now,))
        rows = cursor.fetchall()
        for row in rows:
            target = row[1]
            # Check if any symlink now points to this target
            execute_with_retry(cursor, 'SELECT COUNT(*) as count FROM symlinks WHERE target = ?', (target,))
            count = cursor.fetchone()[0]
            if count == 0 and os.path.exists(target):
                try:
                    if not DRY_RUN:
                        os.remove(target)
                        logger.info(f"❌ Deleted pending target file: {target}")
                    else:
                        logger.info(f"🟠 Dry-run: Would delete pending target file: {target}")
                except Exception as e:
                    logger.error(f'Error deleting pending target {target}: {e}')
            # Remove from pending_deletions table
            execute_with_retry(cursor, 'DELETE FROM pending_deletions WHERE id = ?', (row[0],))
        conn.commit()

def start_pending_deletion_cleanup():
    while True:
        cleanup_pending_deletions()
        time.sleep(60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage symlinks and their targets.")
    parser.add_argument('--dry-run', action='store_true', help="Run the script in dry-run mode without deleting any files.")
    parser.add_argument('--no-confirm', action='store_true', help="Run the script without confirmation prompts.")
    parser.add_argument('--torrents-directories', nargs='+', default=torrents_directories, help="List of torrents directories to check.")
    parser.add_argument('--exclude', nargs='+', default=[], help="List of patterns to exclude from processing.")
    args = parser.parse_args()

    # Override defaults if provided in the command line
    torrents_directories = args.torrents_directories
    exclude_patterns = args.exclude

    # Determine dry_run mode: CLI flag overrides env
    dry_run = args.dry_run or DRY_RUN

    try:
        if RUN_ON_STARTUP:
            main(dry_run, args.no_confirm, exclude_patterns)
        else:
            logger.info("RUN_ON_STARTUP is false. Exiting without running background scan.")
    except KeyboardInterrupt:
        logger.info("🛑 Script terminated by user.")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        logger.debug(traceback.format_exc())

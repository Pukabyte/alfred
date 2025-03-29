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

# Get directories from environment variables
symlink_directory = os.getenv('SYMLINK_DIR')
torrents_directories = os.getenv('TORRENTS_DIR')

if not symlink_directory or not torrents_directories:
    logger.error("Required environment variables SYMLINK_DIR and TORRENTS_DIR must be set")
    sys.exit(1)

# Convert torrents_directories to list and clean up paths
torrents_directories = [path.strip() for path in torrents_directories.split(',') if path.strip()]

# Set up the SQLite database file
db_file = '/app/data/symlinks.db'

# Customize Loguru logger
logger.remove()  # Remove default logger
logger.add(
    sys.stdout,  # Print logs to the console
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <cyan>{message}</cyan>",
    colorize=True,
)

# Add file handler for logging
logger.add(
    '/app/data/symlink_manager.log',
    rotation='10 MB',
    retention='10 days',
    level='INFO',
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
)

# Create table if it doesn't exist
def create_table(conn):
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS symlinks (
        id INTEGER PRIMARY KEY,
        symlink TEXT UNIQUE,
        target TEXT,
        ref_count INTEGER DEFAULT 1
    )
    ''')
    conn.commit()

# Function to upsert symlinks with reference counting
def upsert_symlink(file_path, conn=None):
    if os.path.islink(file_path):
        target = os.readlink(file_path)
        if not os.path.exists(target):
            logger.warning(f"⚠️ Target {target} does not exist for symlink {file_path}")
            return
        try:
            should_close = False
            if conn is None:
                conn = sqlite3.connect(db_file)
                should_close = True
            
            cursor = conn.cursor()
            # Check if the symlink already exists
            cursor.execute('SELECT symlink FROM symlinks WHERE symlink = ?', (file_path,))
            symlink_row = cursor.fetchone()
            if symlink_row:
                logger.info(f"🔗 Symlink {file_path} already exists in the database.")
            else:
                # Check if the target exists
                cursor.execute('SELECT ref_count FROM symlinks WHERE target = ?', (target,))
                target_row = cursor.fetchone()
                if target_row:
                    ref_count = target_row[0] + 1
                    cursor.execute('UPDATE symlinks SET ref_count = ? WHERE target = ?', (ref_count, target))
                else:
                    ref_count = 1
                cursor.execute('INSERT INTO symlinks (symlink, target, ref_count) VALUES (?, ?, ?)', (file_path, target, ref_count))
            
            if should_close:
                conn.commit()
                conn.close()
            
            logger.info(f"🔗 Added/Updated symlink: {file_path} -> {target}")
        except Exception as e:
            logger.error(f"Error updating symlink {file_path}: {e}")
            logger.debug(traceback.format_exc())
            if should_close:
                conn.close()

# Updated function to find and delete non-linked files
def find_non_linked_files(torrents_directories, symlink_directory, dry_run=False, no_confirm=False, exclude_patterns=[]):
    dst_links = set()
    # First, scan all symlinks and add them to the database
    logger.info("🔍 Scanning for existing symlinks...")
    
    # Open a single database connection for batch operations
    with sqlite3.connect(db_file) as conn:
        # Enable WAL mode for better performance
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA cache_size=10000')
        
        # Start transaction
        conn.execute('BEGIN TRANSACTION')
        
        try:
            for root, dirs, files in os.walk(symlink_directory):
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

        for root, dirs, files in os.walk(torrents_directory):
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

    for file_path in unused_files:
        if not os.path.exists(file_path):
            continue  # Skip if the file doesn't exist

        if os.path.isfile(file_path):
            logger.info(f"File {file_path} is not used!")

            if dry_run:
                logger.info(f"🟠 Dry-run: Would delete file: {file_path}")
            else:
                if no_confirm:
                    response = 'y'
                else:
                    response = input(f"Do you want to delete this file '{file_path}'? (y/n): ")
                if response.lower() == 'y':
                    try:
                        os.remove(file_path)
                        deleted_files += 1
                        logger.info(f"File {file_path} deleted!")
                    except Exception as e:
                        logger.error(f"Error deleting file {file_path}: {e}")
                        logger.error(traceback.format_exc())
                else:
                    logger.info(f"File {file_path} not deleted!")
        else:
            # Skip directories or other non-file paths
            continue

    logger.info(f"Total files checked: {total_files}")
    logger.info(f"Total files deleted: {deleted_files}")

# Function to delete missing symlinks' targets
def delete_missing_target(symlink, dry_run):
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT target, ref_count FROM symlinks WHERE symlink = ?', (symlink,))
        row = cursor.fetchone()
        if row:
            target, ref_count = row
            ref_count -= 1
            if ref_count <= 0:
                # Delete the target file if it exists
                if os.path.exists(target):
                    if dry_run:
                        logger.info(f"🟠 Dry-run: Would delete target: {target}")
                    else:
                        try:
                            if os.path.isfile(target) or os.path.islink(target):
                                os.remove(target)
                                logger.info(f"❌ Deleted target: {target}")
                            else:
                                logger.info(f"Target {target} is not a file. Skipping deletion.")
                        except Exception as e:
                            logger.error(f"Error deleting target {target}: {e}")
                            logger.error(traceback.format_exc())
                cursor.execute('DELETE FROM symlinks WHERE target = ?', (target,))
                logger.info(f"❌ Removed symlink entry from database: {symlink} (was pointing to {target})")
            else:
                # Update ref_count and remove symlink entry
                cursor.execute('UPDATE symlinks SET ref_count = ? WHERE target = ?', (ref_count, target))
                cursor.execute('DELETE FROM symlinks WHERE symlink = ?', (symlink,))
                logger.info(f"🔄 Decremented ref_count for target {target}, new ref_count is {ref_count}")
            conn.commit()
        else:
            logger.warning(f"⚠️ Symlink {symlink} not found in the database.")

# Event handler for file system events
class SymlinkEventHandler(FileSystemEventHandler):
    def __init__(self, dry_run):
        self.dry_run = dry_run

    def on_created(self, event):
        try:
            if os.path.islink(event.src_path):
                upsert_symlink(event.src_path)
        except Exception as e:
            logger.error(f"Error handling creation of {event.src_path}: {e}")
            logger.debug(traceback.format_exc())

    def on_modified(self, event):
        try:
            if os.path.islink(event.src_path):
                upsert_symlink(event.src_path)
        except Exception as e:
            logger.error(f"Error handling modification of {event.src_path}: {e}")
            logger.debug(traceback.format_exc())

    def on_deleted(self, event):
        try:
            delete_missing_target(event.src_path, self.dry_run)
        except Exception as e:
            logger.error(f"Error handling deletion of {event.src_path}: {e}")
            logger.debug(traceback.format_exc())

    def on_moved(self, event):
        try:
            # Handle the deletion of the old symlink
            delete_missing_target(event.src_path, self.dry_run)
            # Handle the creation of the new symlink
            if os.path.islink(event.dest_path):
                upsert_symlink(event.dest_path)
        except Exception as e:
            logger.error(f"Error handling movement from {event.src_path} to {event.dest_path}: {e}")
            logger.debug(traceback.format_exc())

# Main function
def main(dry_run, no_confirm, exclude_patterns):
    # Validate paths and permissions
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

    logger.info("🔄 Initializing database...")
    with sqlite3.connect(db_file) as conn:
        create_table(conn)

    logger.info("🟢 Running startup script...")
    find_non_linked_files(
        torrents_directories,
        symlink_directory,
        dry_run,
        no_confirm,
        exclude_patterns
    )

    event_handler = SymlinkEventHandler(dry_run)
    observer = Observer()
    observer.schedule(event_handler, path=symlink_directory, recursive=True)
    observer.start()
    logger.info("🔍 Real-time scanning started. Monitoring symlink changes...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        logger.info("🛑 Real-time scanning stopped by user.")
    observer.join()

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

    try:
        main(args.dry_run, args.no_confirm, exclude_patterns)
    except KeyboardInterrupt:
        logger.info("🛑 Script terminated by user.")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        logger.debug(traceback.format_exc())

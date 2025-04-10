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
symlink_directories = os.getenv('SYMLINK_DIR')
torrents_directories = os.getenv('TORRENTS_DIR')
delete_behavior = os.getenv('DELETE_BEHAVIOR', 'files').lower()
scan_interval = int(os.getenv('SCAN_INTERVAL', '720'))  # Default to 12 hours if not set

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
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS scan_times (
        id INTEGER PRIMARY KEY,
        last_scan_time INTEGER,
        scan_interval INTEGER
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
            cursor.execute('SELECT target FROM symlinks WHERE symlink = ?', (file_path,))
            symlink_row = cursor.fetchone()
            if symlink_row:
                # If the symlink exists but points to a different target, we need to handle that
                old_target = symlink_row[0]
                if old_target != target:
                    # Decrement ref_count for old target
                    cursor.execute('SELECT ref_count FROM symlinks WHERE target = ?', (old_target,))
                    old_ref_count = cursor.fetchone()[0]
                    if old_ref_count > 1:
                        cursor.execute('UPDATE symlinks SET ref_count = ? WHERE target = ?', (old_ref_count - 1, old_target))
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
                        cursor.execute('DELETE FROM symlinks WHERE target = ?', (old_target,))
                    
                    # Update the symlink to point to the new target
                    cursor.execute('UPDATE symlinks SET target = ? WHERE symlink = ?', (target, file_path))
                else:
                    logger.info(f"🔗 Symlink {file_path} already exists in the database with the same target.")
            else:
                # Check if the target exists
                cursor.execute('SELECT ref_count FROM symlinks WHERE target = ?', (target,))
                target_row = cursor.fetchone()
                if target_row:
                    ref_count = target_row[0] + 1
                    cursor.execute('UPDATE symlinks SET ref_count = ? WHERE target = ?', (ref_count, target))
                    cursor.execute('INSERT INTO symlinks (symlink, target, ref_count) VALUES (?, ?, ?)', (file_path, target, ref_count))
                    logger.info(f"🔄 Incremented ref_count for target {target}, new ref_count is {ref_count}")
                else:
                    ref_count = 1
                    cursor.execute('INSERT INTO symlinks (symlink, target, ref_count) VALUES (?, ?, ?)', (file_path, target, ref_count))
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

# Updated function to find and delete non-linked files
def find_non_linked_files(torrents_directories, symlink_directories, dry_run=False, no_confirm=False, exclude_patterns=[]):
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
                        else:
                            parent_dir = os.path.dirname(file_path)
                            import shutil
                            shutil.rmtree(parent_dir)
                            logger.info(f"Parent folder {parent_dir} deleted!")
                        deleted_files += 1
                    except Exception as e:
                        logger.error(f"Error deleting {'file' if delete_behavior == 'files' else 'parent folder'}: {e}")
                        logger.error(traceback.format_exc())
                else:
                    logger.info(f"{'File' if delete_behavior == 'files' else 'Parent folder'} not deleted!")
        else:
            # Skip directories or other non-file paths
            continue

    logger.info(f"Total files checked: {total_files}")
    logger.info(f"Total {'files' if delete_behavior == 'files' else 'parent folders'} deleted: {deleted_files}")

# Function to delete missing symlinks' targets
def delete_missing_target(symlink, dry_run):
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        # First get the target and check remaining references
        cursor.execute('SELECT target FROM symlinks WHERE symlink = ?', (symlink,))
        row = cursor.fetchone()
        if row:
            target = row[0]
            logger.info(f"🔍 Found target {target} for symlink {symlink}")
            
            # Check how many symlinks point to this target
            cursor.execute('SELECT COUNT(*) FROM symlinks WHERE target = ?', (target,))
            total_refs = cursor.fetchone()[0]
            logger.info(f"🔍 Target {target} has {total_refs} references")
            
            if total_refs == 1:
                # This is the last reference, delete the target first
                logger.info(f"🎯 This is the last reference to {target}, will delete target")
                if os.path.exists(target):
                    if dry_run:
                        logger.info(f"🟠 Dry-run: Would delete target: {target}")
                    else:
                        try:
                            if delete_behavior == 'files':
                                # Delete the target file directly
                                os.remove(target)
                                logger.info(f"❌ Deleted file: {target}")
                            else:  # folders
                                # Delete the parent directory of the target
                                parent_dir = os.path.dirname(target)
                                if os.path.exists(parent_dir):
                                    import shutil
                                    shutil.rmtree(parent_dir)
                                    logger.info(f"❌ Deleted parent folder: {parent_dir}")
                                else:
                                    logger.info(f"Parent folder {parent_dir} does not exist. Skipping deletion.")
                        except Exception as e:
                            logger.error(f"Error deleting target {target}: {e}")
                            logger.error(traceback.format_exc())
                            return  # Don't commit if deletion failed
                else:
                    logger.info(f"Target {target} does not exist. Skipping deletion.")
                
                # Remove all entries for this target from the database
                cursor.execute('DELETE FROM symlinks WHERE target = ?', (target,))
                logger.info(f"❌ Removed all database entries for target: {target}")
            else:
                # Just remove this symlink entry
                cursor.execute('DELETE FROM symlinks WHERE symlink = ?', (symlink,))
                logger.info(f"🔄 Target {target} still has {total_refs - 1} references")
            
            conn.commit()
        else:
            logger.warning(f"⚠️ No database entry found for symlink {symlink}")
            # If symlink not found in database, try to get its target and delete it
            try:
                if os.path.islink(symlink):
                    target = os.readlink(symlink)
                    if os.path.exists(target):
                        if dry_run:
                            logger.info(f"🟠 Dry-run: Would delete target: {target}")
                        else:
                            try:
                                if delete_behavior == 'files':
                                    # Delete the target file directly
                                    os.remove(target)
                                    logger.info(f"❌ Deleted file: {target}")
                                else:  # folders
                                    # Delete the parent directory of the target
                                    parent_dir = os.path.dirname(target)
                                    if os.path.exists(parent_dir):
                                        import shutil
                                        shutil.rmtree(parent_dir)
                                        logger.info(f"❌ Deleted parent folder: {parent_dir}")
                                    else:
                                        logger.info(f"Parent folder {parent_dir} does not exist. Skipping deletion.")
                            except Exception as e:
                                logger.error(f"Error deleting target {target}: {e}")
                                logger.error(traceback.format_exc())
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
                upsert_symlink(event.src_path)
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
            with sqlite3.connect(db_file) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT target, ref_count FROM symlinks WHERE symlink = ?', (event.src_path,))
                row = cursor.fetchone()
                if row:
                    target, ref_count = row
                    ref_count -= 1
                    if ref_count <= 0:
                        # Delete the target file if it exists
                        if os.path.exists(target):
                            if not self.dry_run:
                                try:
                                    if delete_behavior == 'files':
                                        if os.path.isfile(target) or os.path.islink(target):
                                            os.remove(target)
                                            logger.info(f"❌ Deleted file: {target}")
                                        else:
                                            logger.info(f"Target {target} is not a file. Skipping deletion.")
                                    else:  # folders
                                        parent_dir = os.path.dirname(target)
                                        if os.path.exists(parent_dir):
                                            import shutil
                                            shutil.rmtree(parent_dir)
                                            logger.info(f"❌ Deleted parent folder: {parent_dir}")
                                        else:
                                            logger.info(f"Parent folder {parent_dir} does not exist. Skipping deletion.")
                                except Exception as e:
                                    logger.error(f"Error deleting target {target}: {e}")
                                    logger.error(traceback.format_exc())
                        cursor.execute('DELETE FROM symlinks WHERE target = ?', (target,))
                        logger.info(f"❌ Removed symlink entry from database: {event.src_path} (was pointing to {target})")
                    else:
                        # Update ref_count and remove symlink entry
                        cursor.execute('UPDATE symlinks SET ref_count = ? WHERE target = ?', (ref_count, target))
                        cursor.execute('DELETE FROM symlinks WHERE symlink = ?', (event.src_path,))
                        logger.info(f"🔄 Decremented ref_count for target {target}, new ref_count is {ref_count}")
                    conn.commit()
                else:
                    logger.debug(f"⚠️ Symlink {event.src_path} not found in the database.")
        except Exception as e:
            logger.error(f"Error handling deletion of {event.src_path}: {e}")
            logger.error(traceback.format_exc())

    def on_moved(self, event):
        try:
            logger.debug(f"📝 Detected move event: {event.src_path} -> {event.dest_path}")
            
            # First handle the new location
            if os.path.islink(event.dest_path):
                logger.info(f"🔗 Symlink moved to: {event.dest_path}")
                # Get the target before updating the database
                target = os.readlink(event.dest_path)
                # Update the database with the new location
                with sqlite3.connect(db_file) as conn:
                    cursor = conn.cursor()
                    # Check if the target exists in the database
                    cursor.execute('SELECT ref_count FROM symlinks WHERE target = ?', (target,))
                    target_row = cursor.fetchone()
                    if target_row:
                        # Increment ref_count since we're adding a new symlink
                        ref_count = target_row[0] + 1
                        cursor.execute('UPDATE symlinks SET ref_count = ? WHERE target = ?', (ref_count, target))
                        # Add the new symlink
                        cursor.execute('INSERT INTO symlinks (symlink, target, ref_count) VALUES (?, ?, ?)', 
                                     (event.dest_path, target, ref_count))
                        logger.info(f"🔄 Updated ref_count for target {target}, new ref_count is {ref_count}")
                    else:
                        # If target doesn't exist, add it as a new entry
                        upsert_symlink(event.dest_path)
                    conn.commit()
            
            # Then handle the old location
            if os.path.exists(event.src_path) and os.path.islink(event.src_path):
                logger.info(f"🔗 Cleaning up old symlink: {event.src_path}")
                # Get the target before deleting
                target = os.readlink(event.src_path)
                with sqlite3.connect(db_file) as conn:
                    cursor = conn.cursor()
                    # Decrement ref_count
                    cursor.execute('SELECT ref_count FROM symlinks WHERE target = ?', (target,))
                    target_row = cursor.fetchone()
                    if target_row:
                        ref_count = target_row[0] - 1
                        if ref_count <= 0:
                            # Delete the target if it exists
                            if os.path.exists(target):
                                if not self.dry_run:
                                    if delete_behavior == 'files':
                                        if os.path.isfile(target):
                                            os.remove(target)
                                            logger.info(f"❌ Deleted file: {target}")
                                        elif os.path.isdir(target):
                                            # For directories, only delete files inside, not the directory itself
                                            for root, _, files in os.walk(target):
                                                for file in files:
                                                    file_path = os.path.join(root, file)
                                                    if os.path.isfile(file_path):
                                                        os.remove(file_path)
                                                        logger.info(f"❌ Deleted file: {file_path}")
                            cursor.execute('DELETE FROM symlinks WHERE target = ?', (target,))
                            logger.info(f"❌ Removed symlink entry from database: {event.src_path} (was pointing to {target})")
                        else:
                            # Update ref_count and remove symlink entry
                            cursor.execute('UPDATE symlinks SET ref_count = ? WHERE target = ?', (ref_count, target))
                            cursor.execute('DELETE FROM symlinks WHERE symlink = ?', (event.src_path,))
                            logger.info(f"🔄 Decremented ref_count for target {target}, new ref_count is {ref_count}")
                    conn.commit()
            
        except Exception as e:
            logger.error(f"Error handling movement from {event.src_path} to {event.dest_path}: {e}")
            logger.error(traceback.format_exc())

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
            with sqlite3.connect(db_file) as conn:
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
    with sqlite3.connect(db_file) as conn:
        create_table(conn)
        
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
        else:
            last_scan_time, _ = get_last_scan_time(conn)
            next_scan = last_scan_time + (scan_interval * 60)
            time_until_next = next_scan - int(time.time())
            logger.info(f"⏳ Skipping initial scan. Next scan in {time_until_next//60} minutes")

    logger.info("")
    logger.info("🔍 Setting up real-time monitoring...")
    logger.info(f"📁 Monitoring symlink directories: {', '.join(symlink_directories)}")
    logger.info(f"📁 Monitoring torrent directories: {', '.join(torrents_directories)}")
    logger.info("")

    # Start background scan if interval is set
    if scan_interval > 0:
        import threading
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

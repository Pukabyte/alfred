from flask import Flask, jsonify, request, send_from_directory, Response
import sqlite3
import os
from pathlib import Path
import time
import subprocess
import sys
from io import StringIO
import contextlib
from datetime import datetime, timedelta
import json
import logging
import traceback
from loguru import logger
import threading

app = Flask(__name__, static_folder='.')

# Database path - use absolute path in Docker container
DB_PATH = '/app/data/symlinks.db'

# Add parent directory to Python path
sys.path.append('/app')

# Grace period for pending deletions (in seconds), configurable via environment variable
PENDING_DELETION_GRACE_SECONDS = int(os.getenv('PENDING_DELETION_GRACE_SECONDS', '60'))  # Default: 1 minute

# Thread-safe scan status indicator
default_scan_status = {
    'running': False,
    'last_started': None,
    'last_finished': None,
    'last_type': None  # 'manual' or 'auto'
}
scan_status = default_scan_status.copy()
scan_status_lock = threading.Lock()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)  # 30 second timeout
    conn.row_factory = sqlite3.Row
    
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

@app.route('/')
def index():
    return send_from_directory('.', 'dashboard.html')

@app.route('/dashboard')
def dashboard():
    return send_from_directory('.', 'dashboard.html')

@app.route('/symlinks')
def symlinks():
    return send_from_directory('.', 'index.html')

@app.route('/styles.css')
def styles():
    return send_from_directory('.', 'styles.css')

@app.route('/script.js')
def script():
    return send_from_directory('.', 'script.js')

@app.route('/dashboard.js')
def dashboard_js():
    return send_from_directory('.', 'dashboard.js')

@app.route('/layout.js')
def layout_js():
    return send_from_directory('.', 'layout.js')

@app.route('/settings.js')
def settings_js():
    return send_from_directory('.', 'settings.js')

@app.route('/assets/<path:filename>')
def serve_asset(filename):
    return send_from_directory('assets', filename)

@app.route('/api/dashboard')
def get_dashboard_data():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Get total symlinks and unique targets
        execute_with_retry(cursor, 'SELECT COUNT(*) as count FROM symlinks')
        total_symlinks = cursor.fetchone()['count']
        
        execute_with_retry(cursor, 'SELECT COUNT(DISTINCT target) as count FROM symlinks')
        unique_targets = cursor.fetchone()['count']
        
        # Get last scan time and interval
        execute_with_retry(cursor, 'SELECT last_scan_time, scan_interval FROM scan_times ORDER BY id DESC LIMIT 1')
        scan_row = cursor.fetchone()
        last_scan_time = scan_row['last_scan_time'] if scan_row else None
        scan_interval = scan_row['scan_interval'] if scan_row else None
        
        # Calculate next scan time
        next_scan_time = None
        if last_scan_time and scan_interval:
            next_scan_time = last_scan_time + (scan_interval * 60)  # Convert minutes to seconds
        
        # Get total deletions
        execute_with_retry(cursor, 'SELECT COUNT(*) as count FROM deletions')
        total_deletions = cursor.fetchone()['count']
        
        # Get scan statistics from the last scan
        files_checked = 0
        files_deleted = 0
        folders_deleted = 0
        
        if last_scan_time:
            execute_with_retry(cursor, '''
                SELECT files_checked, files_deleted, folders_deleted 
                FROM scan_statistics 
                WHERE scan_time = ?
            ''', (last_scan_time,))
            stats_row = cursor.fetchone()
            if stats_row:
                files_checked = stats_row['files_checked']
                files_deleted = stats_row['files_deleted']
                folders_deleted = stats_row['folders_deleted']
        
        return jsonify({
            'total_symlinks': total_symlinks,
            'unique_targets': unique_targets,
            'last_scan': last_scan_time,
            'next_scan': next_scan_time,
            'total_deletions': total_deletions,
            'scan_results': {
                'files_checked': files_checked,
                'files_deleted': files_deleted,
                'folders_deleted': folders_deleted,
                'scan_interval': scan_interval
            }
        })
    finally:
        conn.close()

class StringIOHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.stream = StringIO()

    def emit(self, record):
        msg = self.format(record)
        self.stream.write(msg + '\n')
        self.stream.flush()

class ScanStatusLogFilter(logging.Filter):
    def filter(self, record):
        # Suppress logs for /api/scan-status endpoint
        return '/api/scan-status' not in record.getMessage()

# Suppress Flask's default access logs for /api/scan-status
log = logging.getLogger('werkzeug')
log.addFilter(ScanStatusLogFilter())

@app.route('/api/scan-status')
def get_scan_status():
    with scan_status_lock:
        return jsonify(scan_status)

@app.route('/api/scan', methods=['POST'])
def run_scan():
    data = request.get_json()
    dry_run = data.get('dry_run', False)
    no_confirm = data.get('no_confirm', False)
    
    def generate():
        # Set scan status to running
        with scan_status_lock:
            scan_status['running'] = True
            scan_status['last_started'] = int(time.time())
            scan_status['last_type'] = 'manual'
        output = StringIO()
        handler_id = None
        
        try:
            # Add string IO handler to logger
            handler_id = logger.add(output, format="{message}", catch=False)
            
            # Log initial state
            logger.info("Starting scan...")
            yield "Starting scan...\n"
            
            # Verify environment variables
            symlink_directories = os.getenv('SYMLINK_DIR', '').split(',')
            torrents_directories = os.getenv('TORRENTS_DIR', '').split(',')
            scan_interval = int(os.getenv('SCAN_INTERVAL', '720'))  # Default to 12 hours
            
            if not symlink_directories[0] or not torrents_directories[0]:
                error_msg = "Error: SYMLINK_DIR or TORRENTS_DIR environment variables are not set properly\n"
                logger.error(error_msg)
                yield error_msg
                return
                
            # Log directories being scanned
            logger.info(f"Scanning symlink directories: {', '.join(symlink_directories)}")
            logger.info(f"Scanning torrent directories: {', '.join(torrents_directories)}")
            yield f"Scanning symlink directories: {', '.join(symlink_directories)}\n"
            yield f"Scanning torrent directories: {', '.join(torrents_directories)}\n"
            
            # Import and run the find_non_linked_files function
            sys.path.append('..')
            try:
                from alfred import find_non_linked_files
            except ImportError as e:
                error_msg = f"Error importing alfred module: {str(e)}\n"
                logger.error(error_msg)
                yield error_msg
                return
            
            # Run the scan
            try:
                find_non_linked_files(
                    torrents_directories,
                    symlink_directories,
                    dry_run=dry_run,
                    no_confirm=no_confirm
                )
            except Exception as e:
                error_msg = f"Error during find_non_linked_files: {str(e)}\n"
                logger.error(error_msg)
                logger.error(traceback.format_exc())
                yield error_msg
                return
            
            # Update last scan time and interval
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    # Get current scan interval from settings
                    with open('/app/data/.env', 'r') as f:
                        for line in f:
                            if line.startswith('SCAN_INTERVAL='):
                                scan_interval = int(line.split('=')[1].strip())
                                break
                    cursor.execute('INSERT INTO scan_times (last_scan_time, scan_interval) VALUES (?, ?)', 
                                 (int(time.time()), scan_interval))
                    conn.commit()
            except Exception as e:
                error_msg = f"Error updating scan time in database: {str(e)}\n"
                logger.error(error_msg)
                yield error_msg
                return
            
            # Get the captured output
            scan_output = output.getvalue()
            if scan_output:
                yield scan_output
            
            yield "Scan completed successfully.\n"
            
        except Exception as e:
            error_msg = f"Unexpected error during scan: {str(e)}\nTraceback:\n{traceback.format_exc()}\n"
            logger.error(error_msg)
            yield error_msg
        finally:
            # Set scan status to not running
            with scan_status_lock:
                scan_status['running'] = False
                scan_status['last_finished'] = int(time.time())
            # Remove the handler if it was added
            try:
                if handler_id is not None:
                    logger.remove(handler_id)
            except ValueError:
                pass  # Handler was already removed or invalid
    
    return Response(
        generate(),
        mimetype='text/plain',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'  # Disable response buffering
        }
    )

@app.route('/api/symlinks')
def get_symlinks():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM symlinks')
    symlinks = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(symlinks)

@app.route('/api/symlinks/<path:symlink>')
def get_symlink(symlink):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Normalize the symlink path to ensure consistent comparison
        normalized_symlink = os.path.normpath(symlink)
        execute_with_retry(cursor, 'SELECT * FROM symlinks WHERE symlink = ?', (normalized_symlink,))
        result = cursor.fetchone()
        if not result:
            return jsonify({'error': 'Symlink not found'}), 404
        return jsonify(dict(result))
    except sqlite3.OperationalError as e:
        logger.error(f"Database error fetching symlink {symlink}: {str(e)}")
        return jsonify({'error': 'Database error occurred'}), 500
    except Exception as e:
        logger.error(f"Unexpected error fetching symlink {symlink}: {str(e)}")
        return jsonify({'error': 'An unexpected error occurred'}), 500
    finally:
        conn.close()

@app.route('/api/symlinks/<path:symlink>', methods=['DELETE'])
def delete_symlink(symlink):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        execute_with_retry(cursor, 'SELECT target, ref_count FROM symlinks WHERE symlink = ?', (symlink,))
        result = cursor.fetchone()
        if not result:
            return jsonify({'error': 'Symlink not found'}), 404

        target, ref_count = result['target'], result['ref_count']
        deletion_reason = 'manual_deletion'

        execute_with_retry(cursor, 'DELETE FROM symlinks WHERE symlink = ?', (symlink,))

        if ref_count == 1:
            # Instead of deleting immediately, schedule for deletion
            scheduled_time = int(time.time()) + PENDING_DELETION_GRACE_SECONDS
            execute_with_retry(cursor, '''
                INSERT INTO pending_deletions (target, scheduled_time)
                VALUES (?, ?)
            ''', (target, scheduled_time))
            deletion_reason = 'pending_deletion_scheduled'

        execute_with_retry(cursor, '''
            INSERT INTO deletions (symlink, target, timestamp, reason)
            VALUES (?, ?, ?, ?)
        ''', (symlink, target, int(time.time()), deletion_reason))

        conn.commit()
        return jsonify({
            'message': 'Symlink deleted successfully',
            'details': {
                'symlink': symlink,
                'target': target,
                'was_last_reference': ref_count == 1,
                'target_pending_deletion': ref_count == 1
            }
        })
    except Exception as e:
        conn.rollback()
        return jsonify({'error': f'Error during deletion: {str(e)}'}), 500
    finally:
        conn.close()

def init_db():
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        # Enable WAL mode
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA busy_timeout=30000')
        
        cursor = conn.cursor()
        
        # Create metrics history table
        execute_with_retry(cursor, '''
        CREATE TABLE IF NOT EXISTS metrics_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            total_symlinks INTEGER,
            unique_targets INTEGER,
            total_deletions INTEGER
        )
        ''')
        
        # Check if deletions table exists and has reason column
        execute_with_retry(cursor, "PRAGMA table_info(deletions)")
        columns = cursor.fetchall()
        has_reason = any(col[1] == 'reason' for col in columns)
        
        if not has_reason:
            # Drop the old table if it exists
            execute_with_retry(cursor, "DROP TABLE IF EXISTS deletions")
            # Create new table with reason column
            execute_with_retry(cursor, '''
            CREATE TABLE IF NOT EXISTS deletions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symlink TEXT NOT NULL,
                target TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                reason TEXT
            )
            ''')
        else:
            # Just create the table if it doesn't exist
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
        CREATE TABLE IF NOT EXISTS pending_deletions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            scheduled_time INTEGER NOT NULL
        )
        ''')
        
        conn.commit()

def record_metrics():
    """Record current metrics to the history table"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
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
        conn.close()

@app.route('/api/metrics')
def get_metrics():
    metric = request.args.get('metric', 'total_symlinks')
    time_range = request.args.get('range', 'daily')
    
    # Calculate date ranges
    now = datetime.now()
    if time_range == 'daily':
        start_date = now - timedelta(days=30)  # Last 30 days
        group_by = '%Y-%m-%d'
        interval = 'day'
    elif time_range == 'weekly':
        start_date = now - timedelta(weeks=12)  # Last 12 weeks
        group_by = '%Y-%W'
        interval = 'week'
    else:  # monthly
        start_date = now - timedelta(days=365)  # Last 12 months
        group_by = '%Y-%m'
        interval = 'month'

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get the metrics data grouped by the specified interval
        query = f'''
        SELECT 
            strftime('{group_by}', timestamp) as label,
            MAX({metric}) as value
        FROM metrics_history
        WHERE timestamp >= ?
        GROUP BY strftime('{group_by}', timestamp)
        ORDER BY label ASC
        '''
        
        cur.execute(query, (start_date.strftime('%Y-%m-%d %H:%M:%S'),))
        rows = cur.fetchall()
        
        # Format the data for the chart
        labels = []
        values = []
        for row in rows:
            labels.append(row['label'])
            values.append(row['value'])

    return jsonify({
        'labels': labels,
        'values': values
    })

@app.route('/api/directories')
def get_directories():
    symlink_dirs = os.getenv('SYMLINK_DIR', '').split(',')
    torrent_dirs = os.getenv('TORRENTS_DIR', '').split(',')
    
    def check_directory_status(directory):
        if not os.path.exists(directory):
            return {'path': directory, 'status': 'down'}
        
        # Check if directory has any children
        try:
            has_children = any(os.path.isdir(os.path.join(directory, f)) for f in os.listdir(directory))
            return {'path': directory, 'status': 'up' if has_children else 'down'}
        except Exception:
            return {'path': directory, 'status': 'down'}
    
    symlink_status = [check_directory_status(dir.strip()) for dir in symlink_dirs if dir.strip()]
    torrent_status = [check_directory_status(dir.strip()) for dir in torrent_dirs if dir.strip()]
    
    return jsonify({
        'symlink_directories': symlink_status,
        'torrent_directories': torrent_status
    })

@app.route('/api/deletions')
def get_deletions():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get optional limit parameter, default to 100
        limit = request.args.get('limit', default=100, type=int)
        
        execute_with_retry(cursor, '''
            SELECT id, symlink, target, timestamp, reason
            FROM deletions
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        
        deletions = [dict(row) for row in cursor.fetchall()]
        
        return jsonify({
            'deletions': deletions,
            'total_count': len(deletions)
        })
        
    except Exception as e:
        return jsonify({'error': f'Error fetching deletions: {str(e)}'}), 500
    finally:
        conn.close()

@app.route('/api/backup-symlinks')
def backup_symlinks():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get all symlinks from the database
        execute_with_retry(cursor, 'SELECT symlink, target, ref_count FROM symlinks')
        symlinks = [dict(row) for row in cursor.fetchall()]
        
        # Create a JSON response with the symlinks data
        response = jsonify(symlinks)
        
        # Set headers for file download
        response.headers['Content-Disposition'] = f'attachment; filename=symlinks_backup_{int(time.time())}.json'
        response.headers['Content-Type'] = 'application/json'
        
        return response
        
    except Exception as e:
        return jsonify({'error': f'Error creating backup: {str(e)}'}), 500
    finally:
        conn.close()

@app.route('/api/restore-symlinks', methods=['POST'])
def restore_symlinks():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
            
        file = request.files['file']
        if not file.filename.endswith('.json'):
            return jsonify({'error': 'Invalid file type. Please upload a JSON file.'}), 400
            
        # Read and parse the JSON file
        data = json.loads(file.read())
        
        if not isinstance(data, list):
            return jsonify({'error': 'Invalid backup format'}), 400
            
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Start a transaction
        conn.execute('BEGIN TRANSACTION')
        
        restored_count = 0
        skipped_count = 0
        errors = []
        
        try:
            # Insert restored symlinks
            for symlink in data:
                if not all(key in symlink for key in ['symlink', 'target', 'ref_count']):
                    raise ValueError('Invalid symlink data format')
                
                symlink_path = symlink['symlink']
                target_path = symlink['target']
                
                # Skip if target doesn't exist
                if not os.path.exists(target_path):
                    errors.append(f"Target not found: {target_path}")
                    skipped_count += 1
                    continue
                
                # Skip if symlink already exists
                if os.path.exists(symlink_path):
                    if os.path.islink(symlink_path):
                        current_target = os.readlink(symlink_path)
                        if current_target == target_path:
                            # If symlink exists and points to the same target, just update the database
                            execute_with_retry(cursor, '''
                                INSERT INTO symlinks (symlink, target, ref_count)
                                VALUES (?, ?, ?)
                            ''', (symlink_path, target_path, symlink['ref_count']))
                            restored_count += 1
                            continue
                        else:
                            errors.append(f"Symlink exists but points to different target: {symlink_path}")
                            skipped_count += 1
                            continue
                    else:
                        errors.append(f"Path exists and is not a symlink: {symlink_path}")
                        skipped_count += 1
                        continue
                
                # Create parent directory if it doesn't exist
                symlink_dir = os.path.dirname(symlink_path)
                if not os.path.exists(symlink_dir):
                    try:
                        os.makedirs(symlink_dir, exist_ok=True)
                    except Exception as e:
                        errors.append(f"Failed to create directory {symlink_dir}: {str(e)}")
                        skipped_count += 1
                        continue
                
                # Create the symlink
                try:
                    os.symlink(target_path, symlink_path)
                    execute_with_retry(cursor, '''
                        INSERT INTO symlinks (symlink, target, ref_count)
                        VALUES (?, ?, ?)
                    ''', (symlink_path, target_path, symlink['ref_count']))
                    restored_count += 1
                except Exception as e:
                    errors.append(f"Failed to create symlink {symlink_path}: {str(e)}")
                    skipped_count += 1
                    continue
            
            # Commit the transaction
            conn.commit()
            
            return jsonify({
                'message': 'Symlinks restored successfully',
                'restored_count': restored_count,
                'skipped_count': skipped_count,
                'errors': errors
            })
            
        except Exception as e:
            # Rollback on error
            conn.rollback()
            raise e
            
    except Exception as e:
        return jsonify({'error': f'Error restoring symlinks: {str(e)}'}), 500
    finally:
        conn.close()

@app.route('/settings')
def settings():
    return send_from_directory('.', 'settings.html')

@app.route('/api/settings', methods=['GET'])
def get_settings():
    try:
        settings = {}
        with open('/app/data/.env', 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    settings[key.strip()] = value.strip()
        return jsonify(settings)
    except Exception as e:
        return jsonify({'error': f'Error reading settings: {str(e)}'}), 500

@app.route('/api/settings', methods=['POST'])
def update_settings():
    try:
        settings = request.get_json()
        
        # Validate settings
        required_fields = ['SYMLINK_DIR', 'TORRENTS_DIR', 'DELETE_BEHAVIOR', 'SCAN_INTERVAL']
        for field in required_fields:
            if field not in settings:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        if settings['DELETE_BEHAVIOR'] not in ['files', 'folders']:
            return jsonify({'error': 'DELETE_BEHAVIOR must be either "files" or "folders"'}), 400
        
        try:
            scan_interval = int(settings['SCAN_INTERVAL'])
            if scan_interval < 0:
                return jsonify({'error': 'SCAN_INTERVAL must be a positive number or 0'}), 400
        except ValueError:
            return jsonify({'error': 'SCAN_INTERVAL must be a valid number'}), 400
        
        # Read existing file to preserve comments
        with open('/app/data/.env', 'r') as f:
            lines = f.readlines()
        
        # Update settings while preserving comments
        new_lines = []
        updated_keys = set()
        
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#'):
                key = line.split('=', 1)[0].strip()
                if key in settings:
                    new_lines.append(f"{key}={settings[key]}")
                    updated_keys.add(key)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        
        # Add any new settings that weren't in the file
        for key, value in settings.items():
            if key not in updated_keys:
                new_lines.append(f"{key}={value}")
        
        # Write the updated file
        with open('/app/data/.env', 'w') as f:
            f.write('\n'.join(new_lines) + '\n')
        
        return jsonify({'message': 'Settings updated successfully'})
    except Exception as e:
        return jsonify({'error': f'Error updating settings: {str(e)}'}), 500

@app.route('/api/settings/reload', methods=['POST'])
def reload_settings():
    try:
        # Import the reload function from alfred
        from alfred import reload_env_settings
        
        # Reload the settings
        reload_env_settings()
        
        return jsonify({
            'message': 'Settings reloaded successfully',
            'status': 'success'
        })
    except ImportError as e:
        logger.error(f"Could not import reload function: {e}")
        return jsonify({
            'error': 'Could not import reload function',
            'details': str(e)
        }), 500
    except Exception as e:
        logger.error(f"Error reloading settings: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'error': 'Failed to reload settings',
            'details': str(e)
        }), 500

# Initialize database tables
init_db()

# Set up a background task to record metrics periodically
def start_metrics_recording():
    while True:
        record_metrics()
        time.sleep(3600)  # Record metrics every hour

import threading
metrics_thread = threading.Thread(target=start_metrics_recording, daemon=True)
metrics_thread.start()

if __name__ == '__main__':
    # Suppress all Flask development server logs
    import logging
    import os
    
    # Suppress werkzeug logs (Flask's underlying WSGI server)
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    # Suppress Flask app logs
    flask_log = logging.getLogger('flask')
    flask_log.setLevel(logging.ERROR)
    
    # Suppress Flask-CLI logs
    cli_log = logging.getLogger('flask.cli')
    cli_log.setLevel(logging.ERROR)
    
    # Set environment variable to suppress Flask startup messages
    os.environ['FLASK_ENV'] = 'production'
    os.environ['WERKZEUG_RUN_MAIN'] = 'true'
    
    # Disable debug mode in production
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False) 
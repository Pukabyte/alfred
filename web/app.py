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

app = Flask(__name__, static_folder='.')

# Database path - use absolute path in Docker container
DB_PATH = '/app/data/symlinks.db'

# Add parent directory to Python path
sys.path.append('/app')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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

@app.route('/api/dashboard')
def get_dashboard_data():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get total symlinks and unique targets
    cursor.execute('SELECT COUNT(*) as count FROM symlinks')
    total_symlinks = cursor.fetchone()['count']
    
    cursor.execute('SELECT COUNT(DISTINCT target) as count FROM symlinks')
    unique_targets = cursor.fetchone()['count']
    
    # Get last scan time and interval
    cursor.execute('SELECT last_scan_time, scan_interval FROM scan_times ORDER BY id DESC LIMIT 1')
    scan_row = cursor.fetchone()
    last_scan_time = scan_row['last_scan_time'] if scan_row else None
    scan_interval = scan_row['scan_interval'] if scan_row else None
    
    # Calculate next scan time
    next_scan_time = None
    if last_scan_time and scan_interval:
        next_scan_time = last_scan_time + (scan_interval * 60)  # Convert minutes to seconds
    
    # Get total deletions
    cursor.execute('SELECT COUNT(*) as count FROM deletions')
    total_deletions = cursor.fetchone()['count']
    
    # Get scan results since last scan
    files_checked = 0
    files_deleted = 0
    folders_deleted = 0
    
    if last_scan_time:
        # Count deletions since last scan
        try:
            cursor.execute('''
                SELECT reason, COUNT(*) as count 
                FROM deletions 
                WHERE timestamp >= ? 
                GROUP BY reason
            ''', (last_scan_time,))
            
            for row in cursor.fetchall():
                if row['reason'] == 'unused_file':
                    files_deleted += row['count']
                elif row['reason'] == 'unused_folder':
                    folders_deleted += row['count']
        except sqlite3.OperationalError:
            # If the reason column doesn't exist, just count total deletions
            cursor.execute('''
                SELECT COUNT(*) as count 
                FROM deletions 
                WHERE timestamp >= ?
            ''', (last_scan_time,))
            total_deletions_since_scan = cursor.fetchone()['count']
            files_deleted = total_deletions_since_scan
    
    conn.close()
    
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

@app.route('/api/scan', methods=['POST'])
def run_scan():
    data = request.get_json()
    dry_run = data.get('dry_run', False)
    no_confirm = data.get('no_confirm', False)
    
    def generate():
        # Create a string buffer to capture output
        output = StringIO()
        
        # Redirect stdout to our buffer
        with contextlib.redirect_stdout(output):
            try:
                # Import and run the find_non_linked_files function
                sys.path.append('..')
                from alfred import find_non_linked_files
                
                # Get directories from environment variables
                symlink_directories = os.getenv('SYMLINK_DIR', '').split(',')
                torrents_directories = os.getenv('TORRENTS_DIR', '').split(',')
                
                # Run the scan
                find_non_linked_files(
                    torrents_directories,
                    symlink_directories,
                    dry_run=dry_run,
                    no_confirm=no_confirm
                )
                
                # Update last scan time
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('INSERT INTO scan_times (last_scan_time) VALUES (?)', (int(time.time()),))
                    conn.commit()
                
            except Exception as e:
                yield f"Error during scan: {str(e)}\n"
                return
            
            # Yield the captured output
            yield output.getvalue()
    
    return Response(generate(), mimetype='text/plain')

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
    cursor.execute('SELECT * FROM symlinks WHERE symlink = ?', (symlink,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return jsonify(dict(result))
    return jsonify({'error': 'Symlink not found'}), 404

@app.route('/api/symlinks/<path:symlink>', methods=['DELETE'])
def delete_symlink(symlink):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Get the target and ref_count before deleting
        cursor.execute('SELECT target, ref_count FROM symlinks WHERE symlink = ?', (symlink,))
        result = cursor.fetchone()
        
        if not result:
            conn.close()
            return jsonify({'error': 'Symlink not found'}), 404
        
        target, ref_count = result['target'], result['ref_count']
        deletion_reason = 'manual_deletion'
        
        # Delete the symlink
        cursor.execute('DELETE FROM symlinks WHERE symlink = ?', (symlink,))
        
        # If this was the last reference to the target, delete the target file
        if ref_count == 1:
            try:
                if os.path.exists(target):
                    os.remove(target)
                    deletion_reason = 'last_reference_deleted'
            except Exception as e:
                conn.rollback()
                conn.close()
                return jsonify({'error': f'Error deleting target: {str(e)}'}), 500
        
        # Record the deletion with reason
        cursor.execute('''
            INSERT INTO deletions (symlink, target, timestamp, reason)
            VALUES (?, ?, ?, ?)
        ''', (symlink, target, int(time.time()), deletion_reason))
        
        conn.commit()
        conn.close()
        return jsonify({
            'message': 'Symlink deleted successfully',
            'details': {
                'symlink': symlink,
                'target': target,
                'was_last_reference': ref_count == 1,
                'target_deleted': ref_count == 1 and not os.path.exists(target)
            }
        })
        
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'error': f'Error during deletion: {str(e)}'}), 500

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        # Create metrics history table
        conn.execute('''
        CREATE TABLE IF NOT EXISTS metrics_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            total_symlinks INTEGER,
            unique_targets INTEGER,
            total_deletions INTEGER
        )
        ''')
        
        # Create deletions table
        conn.execute('''
        CREATE TABLE IF NOT EXISTS deletions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symlink TEXT NOT NULL,
            target TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            reason TEXT
        )
        ''')
        
        conn.commit()

def record_metrics():
    """Record current metrics to the history table"""
    with sqlite3.connect(DB_PATH) as conn:
        # Get current stats
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) as total, COUNT(DISTINCT target) as unique_targets FROM symlinks')
        total, unique = cur.fetchone()
        
        # Get total deletions (using the correct table name)
        cur.execute('SELECT COUNT(*) FROM deletions')
        deletions = cur.fetchone()[0]
        
        # Record metrics
        cur.execute('''
        INSERT INTO metrics_history (total_symlinks, unique_targets, total_deletions)
        VALUES (?, ?, ?)
        ''', (total, unique, deletions))
        conn.commit()

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
        
        cursor.execute('''
            SELECT id, symlink, target, timestamp, reason
            FROM deletions
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        
        deletions = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return jsonify({
            'deletions': deletions,
            'total_count': len(deletions)
        })
        
    except Exception as e:
        return jsonify({'error': f'Error fetching deletions: {str(e)}'}), 500

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
    # Disable debug mode in production
    app.run(host='0.0.0.0', port=5000, debug=False) 
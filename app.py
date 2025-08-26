import os
import logging
from flask import Flask, render_template, request, flash, redirect, url_for, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
import json
from tps_core import (
    get_conn, add_raw, process_pending, recent, search_clean, 
    count_stats, get_entry_detail, check_fts_available
)

# Set up logging
logging.basicConfig(level=logging.DEBUG)

# Create the app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "tps-dev-secret-key")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

@app.route('/')
def index():
    """Main dashboard showing recent entries and quick stats"""
    try:
        conn = get_conn()
        recent_entries = recent(conn, 10)
        total, processed, errors = count_stats(conn)
        pending = total - processed - errors
        conn.close()
        
        return render_template('index.html', 
                             recent_entries=recent_entries,
                             total=total, 
                             processed=processed, 
                             errors=errors,
                             pending=pending)
    except Exception as e:
        app.logger.error(f"Error loading dashboard: {e}")
        flash('Error loading dashboard', 'danger')
        return render_template('index.html', 
                             recent_entries=[], 
                             total=0, processed=0, errors=0, pending=0)

@app.route('/entry', methods=['GET', 'POST'])
def entry():
    """Text entry form"""
    if request.method == 'POST':
        text = request.form.get('text', '').strip()
        if not text:
            flash('Please enter some text', 'warning')
            return render_template('entry.html')
        
        try:
            conn = get_conn()
            raw_id = add_raw(conn, text)
            
            # Auto-process if requested
            if request.form.get('auto_process'):
                ok, err = process_pending(conn)
                if ok:
                    flash(f'Text entry added and processed successfully (ID: {raw_id})', 'success')
                else:
                    flash(f'Text entry added (ID: {raw_id}) but processing failed', 'warning')
            else:
                flash(f'Text entry added successfully (ID: {raw_id})', 'success')
            
            conn.close()
            return redirect(url_for('index'))
            
        except Exception as e:
            app.logger.error(f"Error adding entry: {e}")
            flash('Error adding text entry', 'danger')
    
    return render_template('entry.html')

@app.route('/process')
def process():
    """Process pending entries"""
    try:
        conn = get_conn()
        ok, err = process_pending(conn)
        conn.close()
        
        if ok:
            flash(f'Successfully processed {ok} entries', 'success')
        if err:
            flash(f'{err} entries failed to process', 'warning')
        if not ok and not err:
            flash('No pending entries to process', 'info')
            
    except Exception as e:
        app.logger.error(f"Error processing entries: {e}")
        flash('Error processing entries', 'danger')
    
    return redirect(url_for('index'))

@app.route('/search', methods=['GET', 'POST'])
def search():
    """Search cleaned entries"""
    results = []
    query = ''
    
    if request.method == 'POST':
        query = request.form.get('query', '').strip()
        if query:
            try:
                conn = get_conn()
                use_fts = check_fts_available(conn)
                results = search_clean(conn, query, use_fts)
                conn.close()
                
                if not results:
                    flash('No results found', 'info')
                else:
                    flash(f'Found {len(results)} results', 'success')
                    
            except Exception as e:
                app.logger.error(f"Error searching: {e}")
                flash('Error performing search', 'danger')
    
    return render_template('search.html', results=results, query=query)

@app.route('/browse')
def browse():
    """Browse all entries with pagination"""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    
    try:
        conn = get_conn()
        entries = recent(conn, per_page, offset)
        total, processed, errors = count_stats(conn)
        conn.close()
        
        # Calculate pagination info
        total_pages = (total + per_page - 1) // per_page
        has_prev = page > 1
        has_next = page < total_pages
        
        return render_template('browse.html', 
                             entries=entries,
                             page=page,
                             total_pages=total_pages,
                             has_prev=has_prev,
                             has_next=has_next,
                             total=total)
    except Exception as e:
        app.logger.error(f"Error browsing entries: {e}")
        flash('Error loading entries', 'danger')
        return render_template('browse.html', entries=[], page=1, total_pages=0, 
                             has_prev=False, has_next=False, total=0)

@app.route('/entry/<int:entry_id>')
def view_entry(entry_id):
    """View detailed entry information"""
    try:
        conn = get_conn()
        entry = get_entry_detail(conn, entry_id)
        conn.close()
        
        if not entry:
            flash('Entry not found', 'warning')
            return redirect(url_for('browse'))
        
        return render_template('entry_detail.html', entry=entry)
    except Exception as e:
        app.logger.error(f"Error loading entry {entry_id}: {e}")
        flash('Error loading entry', 'danger')
        return redirect(url_for('browse'))

@app.route('/stats')
def stats():
    """Detailed statistics page"""
    try:
        conn = get_conn()
        total, processed, errors = count_stats(conn)
        pending = total - processed - errors
        
        # Get recent activity
        recent_entries = recent(conn, 20)
        conn.close()
        
        return render_template('stats.html', 
                             total=total,
                             processed=processed,
                             errors=errors,
                             pending=pending,
                             recent_entries=recent_entries)
    except Exception as e:
        app.logger.error(f"Error loading stats: {e}")
        flash('Error loading statistics', 'danger')
        return render_template('stats.html', 
                             total=0, processed=0, errors=0, pending=0,
                             recent_entries=[])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

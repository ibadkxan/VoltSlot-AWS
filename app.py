"""
EV Charging Slot Booking System — Flask Backend
Run: python app.py
"""

import hashlib
import logging
import os
import uuid
from functools import wraps
from dotenv import load_dotenv
from flask import Flask, request, jsonify, session, render_template
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db, close_db
from services.notifications import publish_booking_event

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = Flask(__name__)
secret_key = os.getenv("SECRET_KEY")
if os.getenv("APP_ENV", "development").lower() == "production" and not secret_key:
    raise RuntimeError("SECRET_KEY must be set in production")
app.secret_key = secret_key or "local-development-only-change-me"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
)
app.teardown_appcontext(close_db)

@app.context_processor
def inject_asset_helper():
    def asset_url(filename):
        base = os.getenv("ASSET_BASE_URL", "").rstrip("/")
        if base:
            return f"{base}/{filename.lstrip('/')}"
        from flask import url_for
        return url_for("static", filename=filename)
    return {"asset_url": asset_url}

@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "voltslot"}), 200

@app.get("/health/db")
def database_health():
    try:
        cursor = get_db().cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
        return jsonify({"status": "ok", "database": "reachable"}), 200
    except Exception:
        logger.exception("Database health check failed")
        return jsonify({"status": "error", "database": "unavailable"}), 503

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def sha256(text):
    """Legacy hash helper used only to verify old sample accounts."""
    return hashlib.sha256(text.encode()).hexdigest()


def verify_and_upgrade_password(stored_hash, password, table, id_column, record_id):
    """Verify Werkzeug hashes and legacy SHA-256 hashes, upgrading legacy ones."""
    valid = False
    if stored_hash and (stored_hash.startswith("pbkdf2:") or stored_hash.startswith("scrypt:")):
        try:
            valid = check_password_hash(stored_hash, password)
        except (ValueError, TypeError):
            valid = False
    else:
        valid = stored_hash == sha256(password)
        if valid:
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                f"UPDATE {table} SET password_hash=%s WHERE {id_column}=%s",
                (generate_password_hash(password), record_id),
            )
            db.commit()
            cursor.close()
    return valid

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Login required'}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_id' not in session:
            return jsonify({'error': 'Admin login required'}), 401
        return f(*args, **kwargs)
    return decorated

# ─── PAGES ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

# ─── AUTH — USER ──────────────────────────────────────────────────────────────

@app.route('/api/register', methods=['POST'])
def register():
    d = request.json
    required = ['first_name', 'last_name', 'email', 'password', 'phone']
    if not all(d.get(k) for k in required):
        return jsonify({'error': 'All fields are required'}), 400

    if len(d['password']) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT user_id FROM user WHERE email=%s OR phone=%s",
                   (d['email'], d['phone']))
    if cursor.fetchone():
        return jsonify({'error': 'Email or phone already registered'}), 409

    cursor.execute(
        """INSERT INTO user (first_name, last_name, email, password_hash, phone, wallet_balance)
           VALUES (%s, %s, %s, %s, %s, 0.00)""",
        (d['first_name'], d['last_name'], d['email'], generate_password_hash(d['password']), d['phone'])
    )
    db.commit()
    user_id = cursor.lastrowid
    cursor.close()

    session['user_id'] = user_id
    session['user_name'] = f"{d['first_name']} {d['last_name']}"
    return jsonify({'message': 'Registered successfully', 'user_id': user_id,
                    'name': session['user_name']}), 201


@app.route('/api/login', methods=['POST'])
def login():
    d = request.json
    if not d.get('email') or not d.get('password'):
        return jsonify({'error': 'Email and password required'}), 400

    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM user WHERE email=%s", (d['email'],))
    user = cursor.fetchone()
    cursor.close()

    if not user or not verify_and_upgrade_password(
        user['password_hash'], d['password'], 'user', 'user_id', user['user_id']
    ):
        return jsonify({'error': 'Invalid email or password'}), 401

    session['user_id'] = user['user_id']
    session['user_name'] = f"{user['first_name']} {user['last_name']}"
    return jsonify({
        'message': 'Login successful',
        'user_id': user['user_id'],
        'name': session['user_name'],
        'wallet_balance': float(user['wallet_balance'])
    })


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'message': 'Logged out'})


@app.route('/api/me', methods=['GET'])
@login_required
def get_me():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT user_id, first_name, last_name, email, phone, wallet_balance, created_at FROM user WHERE user_id=%s",
        (session['user_id'],)
    )
    user = cursor.fetchone()
    cursor.close()
    if user:
        user['wallet_balance'] = float(user['wallet_balance'])
        user['created_at'] = str(user['created_at'])
    return jsonify(user)

# ─── AUTH — ADMIN ─────────────────────────────────────────────────────────────

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    d = request.json
    if not d.get('email') or not d.get('password'):
        return jsonify({'error': 'Email and password required'}), 400

    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM admin WHERE email=%s", (d['email'],))
    admin = cursor.fetchone()
    cursor.close()

    if not admin or not verify_and_upgrade_password(
        admin['password_hash'], d['password'], 'admin', 'admin_id', admin['admin_id']
    ):
        return jsonify({'error': 'Invalid admin credentials'}), 401

    session.clear()
    session['admin_id'] = admin['admin_id']
    session['admin_name'] = admin['name']
    session['admin_role'] = admin['role']
    return jsonify({
        'message': 'Admin login successful',
        'admin_id': admin['admin_id'],
        'name': admin['name'],
        'role': admin['role']
    })


@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    session.clear()
    return jsonify({'message': 'Admin logged out'})

# ─── STATIONS ─────────────────────────────────────────────────────────────────

@app.route('/api/stations', methods=['GET'])
def get_stations():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        """SELECT station_id, station_name, address, city, state, pincode,
                  total_slots, price_per_unit, status
           FROM charging_station
           WHERE status = 'active'
           ORDER BY station_name"""
    )
    stations = cursor.fetchall()
    cursor.close()
    for s in stations:
        s['price_per_unit'] = float(s['price_per_unit'])
    return jsonify(stations)


@app.route('/api/stations/all', methods=['GET'])
@admin_required
def get_all_stations():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        """SELECT cs.*, a.name AS managed_by,
                  COUNT(sb.booking_id) AS total_bookings,
                  COALESCE(SUM(CASE WHEN sb.booking_status != 'Cancelled' THEN sb.amount ELSE 0 END), 0) AS total_revenue
           FROM charging_station cs
           JOIN admin a ON cs.admin_id = a.admin_id
           LEFT JOIN slot_booking sb ON cs.station_id = sb.station_id
           GROUP BY cs.station_id
           ORDER BY cs.station_name"""
    )
    stations = cursor.fetchall()
    cursor.close()
    for s in stations:
        s['price_per_unit'] = float(s['price_per_unit'])
        s['total_revenue'] = float(s['total_revenue'])
    return jsonify(stations)


@app.route('/api/stations', methods=['POST'])
@admin_required
def add_station():
    d = request.json
    required = ['station_name', 'address', 'city', 'state', 'pincode', 'total_slots', 'price_per_unit']
    if not all(d.get(k) for k in required):
        return jsonify({'error': 'All fields required'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """INSERT INTO charging_station
           (admin_id, station_name, address, city, state, pincode, total_slots, price_per_unit, status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'active')""",
        (session['admin_id'], d['station_name'], d['address'], d['city'],
         d['state'], d['pincode'], d['total_slots'], d['price_per_unit'])
    )
    db.commit()
    station_id = cursor.lastrowid
    cursor.close()
    return jsonify({'message': 'Station added', 'station_id': station_id}), 201


@app.route('/api/stations/<int:station_id>/status', methods=['PUT'])
@admin_required
def update_station_status(station_id):
    d = request.json
    new_status = d.get('status')
    if new_status not in ('active', 'inactive', 'maintenance'):
        return jsonify({'error': 'Invalid status'}), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE charging_station SET status=%s WHERE station_id=%s",
                   (new_status, station_id))
    db.commit()
    cursor.close()
    return jsonify({'message': f'Station status updated to {new_status}'})

# ─── VEHICLES ─────────────────────────────────────────────────────────────────

@app.route('/api/vehicles', methods=['GET'])
@login_required
def get_vehicles():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM vehicle WHERE user_id=%s ORDER BY created_at DESC",
        (session['user_id'],)
    )
    vehicles = cursor.fetchall()
    cursor.close()
    for v in vehicles:
        v['bat_cap_kwh'] = float(v['bat_cap_kwh'])
        v['created_at'] = str(v['created_at'])
    return jsonify(vehicles)


@app.route('/api/vehicles', methods=['POST'])
@login_required
def add_vehicle():
    d = request.json
    required = ['reg_number', 'brand', 'model', 'ev_type', 'bat_cap_kwh', 'plug_type']
    if not all(d.get(k) for k in required):
        return jsonify({'error': 'All fields required'}), 400

    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT vehicle_id FROM vehicle WHERE reg_number=%s", (d['reg_number'],))
    if cursor.fetchone():
        return jsonify({'error': 'Vehicle registration number already exists'}), 409

    cursor.execute(
        """INSERT INTO vehicle (user_id, reg_number, brand, model, ev_type, bat_cap_kwh, plug_type)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (session['user_id'], d['reg_number'].upper(), d['brand'], d['model'],
         d['ev_type'], d['bat_cap_kwh'], d['plug_type'])
    )
    db.commit()
    vid = cursor.lastrowid
    cursor.close()
    return jsonify({'message': 'Vehicle added', 'vehicle_id': vid}), 201

# ─── BOOKINGS — USER ──────────────────────────────────────────────────────────

@app.route('/api/bookings', methods=['GET'])
@login_required
def get_bookings():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        """SELECT sb.*, cs.station_name, cs.city, cs.price_per_unit,
                  v.reg_number, v.brand, v.model
           FROM slot_booking sb
           JOIN charging_station cs ON sb.station_id = cs.station_id
           JOIN vehicle v ON sb.vehicle_id = v.vehicle_id
           WHERE sb.user_id = %s
           ORDER BY sb.booked_at DESC""",
        (session['user_id'],)
    )
    bookings = cursor.fetchall()
    cursor.close()
    for b in bookings:
        b['amount'] = float(b['amount'])
        b['units_consumed'] = float(b['units_consumed'])
        b['price_per_unit'] = float(b['price_per_unit'])
        b['slot_date'] = str(b['slot_date'])
        b['start_time'] = str(b['start_time'])
        b['end_time'] = str(b['end_time'])
        b['booked_at'] = str(b['booked_at'])
    return jsonify(bookings)


@app.route('/api/bookings', methods=['POST'])
@login_required
def book_slot():
    d = request.json
    required = ['vehicle_id', 'station_id', 'slot_date', 'start_time', 'end_time',
                 'charger_type', 'payment_method']
    if not all(d.get(k) for k in required):
        return jsonify({'error': 'All booking fields are required'}), 400

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Verify vehicle belongs to user
    cursor.execute("SELECT * FROM vehicle WHERE vehicle_id=%s AND user_id=%s",
                   (d['vehicle_id'], session['user_id']))
    if not cursor.fetchone():
        return jsonify({'error': 'Vehicle not found'}), 404

    # Get station info
    cursor.execute("SELECT * FROM charging_station WHERE station_id=%s AND status='active'",
                   (d['station_id'],))
    station = cursor.fetchone()
    if not station:
        return jsonify({'error': 'Station not found or inactive'}), 404

    if station['total_slots'] <= 0:
        return jsonify({'error': 'No slots available at this station'}), 409

    # Check wallet if payment method is Wallet
    if d['payment_method'] == 'Wallet':
        cursor.execute("SELECT wallet_balance FROM user WHERE user_id=%s", (session['user_id'],))
        user = cursor.fetchone()
        # Minimum balance check (we'll do final deduction on Completed)
        if float(user['wallet_balance']) < 10:
            return jsonify({'error': 'Insufficient wallet balance (minimum ₹10 required)'}), 400

    # Get admin_id for station
    admin_id = station['admin_id']
    tx_id = str(uuid.uuid4())[:20].replace('-', '').upper() if d['payment_method'] != 'Wallet' else None

    try:
        cursor.execute(
            """INSERT INTO slot_booking
               (user_id, vehicle_id, station_id, admin_id, slot_date, start_time, end_time,
                charger_type, payment_method, tx_id, tx_status, booking_status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'Pending','Confirmed')""",
            (session['user_id'], d['vehicle_id'], d['station_id'], admin_id,
             d['slot_date'], d['start_time'], d['end_time'],
             d['charger_type'], d['payment_method'], tx_id)
        )
        db.commit()
        booking_id = cursor.lastrowid
        cursor.close()
        publish_booking_event(
            "BOOKING_CONFIRMED",
            booking_id=booking_id,
            customer_name=session.get('user_name', 'VoltSlot customer'),
            station_name=station.get('station_name', 'Charging station'),
            booking_date=str(d['slot_date']),
            start_time=str(d['start_time']),
            end_time=str(d['end_time']),
            payment_method=d['payment_method'],
        )
        return jsonify({'message': 'Booking confirmed!', 'booking_id': booking_id,
                        'tx_id': tx_id}), 201
    except Exception as e:
        db.rollback()
        err = str(e)
        if 'already booked' in err.lower() or 'time slot' in err.lower():
            return jsonify({'error': 'This time slot is already booked at the station'}), 409
        return jsonify({'error': 'Booking failed: ' + err}), 500


@app.route('/api/bookings/<int:booking_id>/cancel', methods=['PUT'])
@login_required
def cancel_booking(booking_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM slot_booking WHERE booking_id=%s AND user_id=%s",
        (booking_id, session['user_id'])
    )
    booking = cursor.fetchone()
    if not booking:
        return jsonify({'error': 'Booking not found'}), 404
    if booking['booking_status'] in ('Cancelled', 'Completed'):
        return jsonify({'error': f"Cannot cancel a {booking['booking_status']} booking"}), 400

    cursor.execute(
        "UPDATE slot_booking SET booking_status='Cancelled' WHERE booking_id=%s",
        (booking_id,)
    )
    db.commit()
    cursor.close()
    publish_booking_event("BOOKING_CANCELLED", booking_id=booking_id,
                          customer_name=session.get('user_name', 'VoltSlot customer'))
    return jsonify({'message': 'Booking cancelled successfully'})


@app.route('/api/bookings/check-availability', methods=['POST'])
def check_availability():
    """Check if a time slot is available at a station."""
    d = request.json
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute(
        """SELECT COUNT(*) AS cnt FROM slot_booking
           WHERE station_id=%s AND slot_date=%s
             AND booking_status NOT IN ('Cancelled')
             AND (
                 (%s >= start_time AND %s < end_time) OR
                 (%s > start_time AND %s <= end_time) OR
                 (%s <= start_time AND %s >= end_time)
             )""",
        (d['station_id'], d['slot_date'],
         d['start_time'], d['start_time'],
         d['end_time'],   d['end_time'],
         d['start_time'], d['end_time'])
    )
    result = cursor.fetchone()

    # Also get station price for cost estimate
    cursor.execute(
        "SELECT price_per_unit, total_slots FROM charging_station WHERE station_id=%s",
        (d['station_id'],)
    )
    station = cursor.fetchone()
    cursor.close()

    available = result['cnt'] == 0 and station and station['total_slots'] > 0

    # Estimate cost from time difference
    estimated_cost = None
    if station and d.get('units'):
        estimated_cost = round(float(station['price_per_unit']) * float(d['units']), 2)

    return jsonify({
        'available': available,
        'slots_left': station['total_slots'] if station else 0,
        'price_per_unit': float(station['price_per_unit']) if station else 0,
        'estimated_cost': estimated_cost
    })

# ─── BOOKINGS — ADMIN ─────────────────────────────────────────────────────────

@app.route('/api/admin/bookings', methods=['GET'])
@admin_required
def admin_get_bookings():
    status_filter = request.args.get('status', '')
    db = get_db()
    cursor = db.cursor(dictionary=True)

    query = """
        SELECT sb.*, CONCAT(u.first_name,' ',u.last_name) AS user_name, u.email AS user_email,
               v.reg_number, v.brand, v.model,
               cs.station_name, cs.city
        FROM slot_booking sb
        JOIN user u ON sb.user_id = u.user_id
        JOIN vehicle v ON sb.vehicle_id = v.vehicle_id
        JOIN charging_station cs ON sb.station_id = cs.station_id
    """
    params = []
    if status_filter:
        query += " WHERE sb.booking_status = %s"
        params.append(status_filter)
    query += " ORDER BY sb.booked_at DESC"

    cursor.execute(query, params)
    bookings = cursor.fetchall()
    cursor.close()

    for b in bookings:
        b['amount'] = float(b['amount'])
        b['units_consumed'] = float(b['units_consumed'])
        b['slot_date'] = str(b['slot_date'])
        b['start_time'] = str(b['start_time'])
        b['end_time'] = str(b['end_time'])
        b['booked_at'] = str(b['booked_at'])
    return jsonify(bookings)


@app.route('/api/admin/bookings/<int:booking_id>', methods=['PUT'])
@admin_required
def admin_update_booking(booking_id):
    d = request.json
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT * FROM slot_booking WHERE booking_id=%s", (booking_id,))
    booking = cursor.fetchone()
    if not booking:
        return jsonify({'error': 'Booking not found'}), 404

    new_status = d.get('booking_status', booking['booking_status'])
    units = d.get('units_consumed', booking['units_consumed'])

    try:
        cursor.execute(
            """UPDATE slot_booking
               SET booking_status=%s, units_consumed=%s
               WHERE booking_id=%s""",
            (new_status, units, booking_id)
        )
        db.commit()
        cursor.close()
        return jsonify({'message': 'Booking updated successfully'})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500

# ─── ADMIN — USERS & VEHICLES ─────────────────────────────────────────────────

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def admin_get_users():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        """SELECT u.user_id, u.first_name, u.last_name, u.email, u.phone,
                  u.wallet_balance, u.created_at,
                  COUNT(sb.booking_id) AS total_bookings
           FROM user u
           LEFT JOIN slot_booking sb ON u.user_id = sb.user_id
           GROUP BY u.user_id
           ORDER BY u.created_at DESC"""
    )
    users = cursor.fetchall()
    cursor.close()
    for u in users:
        u['wallet_balance'] = float(u['wallet_balance'])
        u['created_at'] = str(u['created_at'])
    return jsonify(users)


@app.route('/api/admin/vehicles', methods=['GET'])
@admin_required
def admin_get_vehicles():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        """SELECT v.*, CONCAT(u.first_name,' ',u.last_name) AS owner_name, u.email
           FROM vehicle v
           JOIN user u ON v.user_id = u.user_id
           ORDER BY v.created_at DESC"""
    )
    vehicles = cursor.fetchall()
    cursor.close()
    for v in vehicles:
        v['bat_cap_kwh'] = float(v['bat_cap_kwh'])
        v['created_at'] = str(v['created_at'])
    return jsonify(vehicles)

# ─── WALLET ───────────────────────────────────────────────────────────────────

@app.route('/api/wallet', methods=['GET'])
@login_required
def get_wallet():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT wallet_balance FROM user WHERE user_id=%s", (session['user_id'],))
    user = cursor.fetchone()
    cursor.execute(
        """SELECT * FROM wallet_transaction WHERE user_id=%s ORDER BY txn_at DESC LIMIT 50""",
        (session['user_id'],)
    )
    transactions = cursor.fetchall()
    cursor.close()
    for t in transactions:
        t['amount'] = float(t['amount'])
        t['txn_at'] = str(t['txn_at'])
    return jsonify({
        'balance': float(user['wallet_balance']),
        'transactions': transactions
    })


@app.route('/api/wallet/topup', methods=['POST'])
@login_required
def wallet_topup():
    d = request.json
    amount = d.get('amount', 0)
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid amount'}), 400

    if amount < 10 or amount > 50000:
        return jsonify({'error': 'Amount must be between ₹10 and ₹50,000'}), 400

    db = get_db()
    cursor = db.cursor(dictionary=True)
    # The trg_wallet_topup trigger automatically logs the transaction
    cursor.execute(
        "UPDATE user SET wallet_balance = wallet_balance + %s WHERE user_id=%s",
        (amount, session['user_id'])
    )
    db.commit()

    cursor.execute("SELECT wallet_balance FROM user WHERE user_id=%s", (session['user_id'],))
    user = cursor.fetchone()
    cursor.close()
    return jsonify({
        'message': f'₹{amount:.2f} added to wallet',
        'new_balance': float(user['wallet_balance'])
    })

# ─── ADMIN DASHBOARD STATS ────────────────────────────────────────────────────

@app.route('/api/admin/stats', methods=['GET'])
@admin_required
def admin_stats():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) AS cnt FROM user")
    total_users = cursor.fetchone()['cnt']

    cursor.execute("SELECT COUNT(*) AS cnt FROM slot_booking WHERE booking_status != 'Cancelled'")
    total_bookings = cursor.fetchone()['cnt']

    cursor.execute("SELECT COALESCE(SUM(amount),0) AS rev FROM slot_booking WHERE booking_status='Completed'")
    total_revenue = float(cursor.fetchone()['rev'])

    cursor.execute("SELECT COUNT(*) AS cnt FROM charging_station WHERE status='active'")
    active_stations = cursor.fetchone()['cnt']

    cursor.close()
    return jsonify({
        'total_users': total_users,
        'total_bookings': total_bookings,
        'total_revenue': total_revenue,
        'active_stations': active_stations
    })

# ─── RUN ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '5000')), debug=False)

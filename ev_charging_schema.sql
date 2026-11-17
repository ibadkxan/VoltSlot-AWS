-- =============================================================================
--  EV CHARGING SLOT BOOKING SYSTEM
--  Database Schema  |  MySQL  |  DBMS Mini Project
--  Normalization: 3NF applied throughout
--  Includes: Tables, Constraints, Indexes, Triggers, Sample Data
-- =============================================================================

CREATE DATABASE IF NOT EXISTS ev_charging_db;
USE ev_charging_db;

-- =============================================================================
--  TABLE 1: ADMIN
--  Stores administrator accounts who manage the system.
-- =============================================================================
CREATE TABLE admin (
    admin_id       INT AUTO_INCREMENT PRIMARY KEY,
    name           VARCHAR(80)  NOT NULL,
    email          VARCHAR(100) NOT NULL UNIQUE,
    password_hash  VARCHAR(255) NOT NULL,
    role           ENUM('super_admin', 'station_admin') DEFAULT 'station_admin',
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
--  TABLE 2: CHARGING_STATION
--  Stores details of each physical EV charging station.
--  3NF: city, state separated to avoid transitive dependency on pincode.
-- =============================================================================
CREATE TABLE charging_station (
    station_id     INT AUTO_INCREMENT PRIMARY KEY,
    admin_id       INT          NOT NULL,
    station_name   VARCHAR(100) NOT NULL,
    address        TEXT         NOT NULL,
    city           VARCHAR(60)  NOT NULL,
    state          VARCHAR(60)  NOT NULL,
    pincode        VARCHAR(10)  NOT NULL,
    total_slots    INT          NOT NULL DEFAULT 1,
    price_per_unit DECIMAL(8,2) NOT NULL COMMENT 'Price per kWh in INR',
    status         ENUM('active','inactive','maintenance') DEFAULT 'active',
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_station_admin FOREIGN KEY (admin_id)
        REFERENCES admin(admin_id) ON DELETE RESTRICT
);

-- =============================================================================
--  TABLE 3: USER
--  Stores registered users of the platform.
-- =============================================================================
CREATE TABLE user (
    user_id        INT AUTO_INCREMENT PRIMARY KEY,
    first_name     VARCHAR(50)  NOT NULL,
    last_name      VARCHAR(50)  NOT NULL,
    email          VARCHAR(100) NOT NULL UNIQUE,
    password_hash  VARCHAR(255) NOT NULL,
    phone          VARCHAR(15)  NOT NULL UNIQUE,
    wallet_balance DECIMAL(10,2) DEFAULT 0.00,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
--  TABLE 4: VEHICLE
--  Stores EV details registered by users.
--  3NF: plug_type stored here to avoid repetition in bookings.
-- =============================================================================
CREATE TABLE vehicle (
    vehicle_id     INT AUTO_INCREMENT PRIMARY KEY,
    user_id        INT          NOT NULL,
    reg_number     VARCHAR(20)  NOT NULL UNIQUE,
    brand          VARCHAR(50)  NOT NULL,
    model          VARCHAR(50)  NOT NULL,
    ev_type        ENUM('2-Wheeler','4-Wheeler','Bus','Other') NOT NULL,
    bat_cap_kwh    DECIMAL(6,2) NOT NULL COMMENT 'Battery capacity in kWh',
    plug_type      ENUM('CCS','CHAdeMO','Type-2','AC-Slow') NOT NULL,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_vehicle_user FOREIGN KEY (user_id)
        REFERENCES user(user_id) ON DELETE CASCADE
);

-- =============================================================================
--  TABLE 5: SLOT_BOOKING  (Central / Core Entity)
--  Records every charging slot booking made in the system.
--  Payment details merged here (normalized — no payment anomalies since
--  each booking has exactly one payment, 1:1 dependency).
-- =============================================================================
CREATE TABLE slot_booking (
    booking_id      INT AUTO_INCREMENT PRIMARY KEY,
    user_id         INT          NOT NULL,
    vehicle_id      INT          NOT NULL,
    station_id      INT          NOT NULL,
    admin_id        INT          NOT NULL,
    slot_date       DATE         NOT NULL,
    start_time      TIME         NOT NULL,
    end_time        TIME         NOT NULL,
    charger_type    ENUM('Fast','Slow','Ultra-Fast') NOT NULL,
    units_consumed  DECIMAL(6,2) DEFAULT 0.00 COMMENT 'kWh consumed',
    amount          DECIMAL(10,2) DEFAULT 0.00 COMMENT 'Total bill in INR',
    payment_method  ENUM('UPI','Card','Wallet','NetBanking') DEFAULT 'UPI',
    tx_id           VARCHAR(100) UNIQUE,
    tx_status       ENUM('Pending','Success','Failed','Refunded') DEFAULT 'Pending',
    booking_status  ENUM('Pending','Confirmed','Cancelled','Completed') DEFAULT 'Pending',
    booked_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_booking_user    FOREIGN KEY (user_id)
        REFERENCES user(user_id) ON DELETE RESTRICT,
    CONSTRAINT fk_booking_vehicle FOREIGN KEY (vehicle_id)
        REFERENCES vehicle(vehicle_id) ON DELETE RESTRICT,
    CONSTRAINT fk_booking_station FOREIGN KEY (station_id)
        REFERENCES charging_station(station_id) ON DELETE RESTRICT,
    CONSTRAINT fk_booking_admin   FOREIGN KEY (admin_id)
        REFERENCES admin(admin_id) ON DELETE RESTRICT,
    CONSTRAINT chk_time CHECK (end_time > start_time)
);

-- =============================================================================
--  TABLE 6: BOOKING_LOG  (Audit Trail — 3NF support table)
--  Tracks every status change on a booking for audit purposes.
--  Separated from slot_booking to maintain 3NF.
-- =============================================================================
CREATE TABLE booking_log (
    log_id         INT AUTO_INCREMENT PRIMARY KEY,
    booking_id     INT          NOT NULL,
    old_status     VARCHAR(30),
    new_status     VARCHAR(30),
    changed_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    remarks        TEXT,
    CONSTRAINT fk_log_booking FOREIGN KEY (booking_id)
        REFERENCES slot_booking(booking_id) ON DELETE CASCADE
);

-- =============================================================================
--  TABLE 7: WALLET_TRANSACTION  (3NF — separated from user table)
--  Tracks wallet top-ups and deductions per user.
-- =============================================================================
CREATE TABLE wallet_transaction (
    txn_id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id        INT          NOT NULL,
    booking_id     INT          DEFAULT NULL,
    txn_type       ENUM('Credit','Debit') NOT NULL,
    amount         DECIMAL(10,2) NOT NULL,
    description    VARCHAR(200),
    txn_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_wallet_user    FOREIGN KEY (user_id)
        REFERENCES user(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_wallet_booking FOREIGN KEY (booking_id)
        REFERENCES slot_booking(booking_id) ON DELETE SET NULL
);

-- =============================================================================
--  INDEXES  (for query performance)
-- =============================================================================
CREATE INDEX idx_booking_user    ON slot_booking(user_id);
CREATE INDEX idx_booking_station ON slot_booking(station_id);
CREATE INDEX idx_booking_date    ON slot_booking(slot_date);
CREATE INDEX idx_vehicle_user    ON vehicle(user_id);
CREATE INDEX idx_station_admin   ON charging_station(admin_id);

-- =============================================================================
--  TRIGGER 1: trg_after_booking_insert
--  When a new booking is inserted, auto-log it and decrement available slots.
-- =============================================================================
DELIMITER $$
CREATE TRIGGER trg_after_booking_insert
AFTER INSERT ON slot_booking
FOR EACH ROW
BEGIN
    INSERT INTO booking_log(booking_id, old_status, new_status, remarks)
    VALUES (NEW.booking_id, NULL, NEW.booking_status, 'Booking created');

    UPDATE charging_station
    SET total_slots = total_slots - 1
    WHERE station_id = NEW.station_id AND total_slots > 0;
END$$
DELIMITER ;

-- =============================================================================
--  TRIGGER 2: trg_after_booking_status_update
--  Logs every status change and restores slot if booking is cancelled.
-- =============================================================================
DELIMITER $$
CREATE TRIGGER trg_after_booking_status_update
AFTER UPDATE ON slot_booking
FOR EACH ROW
BEGIN
    IF OLD.booking_status <> NEW.booking_status THEN
        INSERT INTO booking_log(booking_id, old_status, new_status, remarks)
        VALUES (NEW.booking_id, OLD.booking_status, NEW.booking_status,
                CONCAT('Status changed from ', OLD.booking_status, ' to ', NEW.booking_status));

        -- Restore slot count on cancellation
        IF NEW.booking_status = 'Cancelled' THEN
            UPDATE charging_station
            SET total_slots = total_slots + 1
            WHERE station_id = NEW.station_id;
        END IF;
    END IF;
END$$
DELIMITER ;

-- =============================================================================
--  TRIGGER 3: trg_after_booking_complete
--  When booking is Completed, calculate bill and deduct from wallet if used.
-- =============================================================================
DELIMITER $$
CREATE TRIGGER trg_after_booking_complete
BEFORE UPDATE ON slot_booking
FOR EACH ROW
BEGIN
    DECLARE v_price DECIMAL(8,2);

    IF NEW.booking_status = 'Completed' AND OLD.booking_status <> 'Completed' THEN
        SELECT price_per_unit INTO v_price
        FROM charging_station WHERE station_id = NEW.station_id;

        SET NEW.amount = NEW.units_consumed * v_price;

        IF NEW.payment_method = 'Wallet' THEN
            UPDATE user
            SET wallet_balance = wallet_balance - NEW.amount
            WHERE user_id = NEW.user_id;

            INSERT INTO wallet_transaction(user_id, booking_id, txn_type, amount, description)
            VALUES (NEW.user_id, NEW.booking_id, 'Debit', NEW.amount,
                    CONCAT('Charging fee for booking #', NEW.booking_id));
        END IF;
    END IF;
END$$
DELIMITER ;

-- =============================================================================
--  TRIGGER 4: trg_prevent_double_booking
--  Prevents booking same station slot at overlapping times on same date.
-- =============================================================================
DELIMITER $$
CREATE TRIGGER trg_prevent_double_booking
BEFORE INSERT ON slot_booking
FOR EACH ROW
BEGIN
    DECLARE v_count INT;
    SELECT COUNT(*) INTO v_count
    FROM slot_booking
    WHERE station_id = NEW.station_id
      AND slot_date   = NEW.slot_date
      AND booking_status NOT IN ('Cancelled')
      AND (
          (NEW.start_time >= start_time AND NEW.start_time < end_time) OR
          (NEW.end_time > start_time AND NEW.end_time <= end_time) OR
          (NEW.start_time <= start_time AND NEW.end_time >= end_time)
      );
    IF v_count > 0 THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'ERROR: Time slot already booked at this station.';
    END IF;
END$$
DELIMITER ;

-- =============================================================================
--  TRIGGER 5: trg_wallet_topup
--  Logs every wallet credit transaction automatically.
-- =============================================================================
DELIMITER $$
CREATE TRIGGER trg_wallet_topup
AFTER UPDATE ON user
FOR EACH ROW
BEGIN
    IF NEW.wallet_balance > OLD.wallet_balance THEN
        INSERT INTO wallet_transaction(user_id, txn_type, amount, description)
        VALUES (NEW.user_id, 'Credit',
                NEW.wallet_balance - OLD.wallet_balance,
                'Wallet top-up');
    END IF;
END$$
DELIMITER ;

-- =============================================================================
--  SAMPLE DATA
-- =============================================================================

-- Admins
INSERT INTO admin(name, email, password_hash, role) VALUES
('Super Admin',    'admin@evcharge.in',   SHA2('Admin@123', 256), 'super_admin'),
('Station Manager','manager@evcharge.in', SHA2('Mgr@456',   256), 'station_admin');

-- Charging Stations
INSERT INTO charging_station(admin_id, station_name, address, city, state, pincode, total_slots, price_per_unit, status) VALUES
(1, 'EV Hub - MG Road',        '45, MG Road',          'Bengaluru', 'Karnataka', '560001', 8,  12.50, 'active'),
(1, 'GreenCharge - Koramangala','12, 5th Block',        'Bengaluru', 'Karnataka', '560034', 6,  11.00, 'active'),
(2, 'PowerUp - Mysuru Road',   '78, Mysuru Road',       'Bengaluru', 'Karnataka', '560026', 4,  10.00, 'active'),
(2, 'VoltStop - Whitefield',   'ITPL Main Road',        'Bengaluru', 'Karnataka', '560066', 10, 13.00, 'active'),
(1, 'ChargePoint - Manipal',   'Near MIT Campus',       'Manipal',   'Karnataka', '576104', 5,  9.50,  'active');

-- Users
INSERT INTO user(first_name, last_name, email, password_hash, phone, wallet_balance) VALUES
('Anish',   'Kumar',   'anish@gmail.com',   SHA2('Anish@123',  256), '9876543210', 500.00),
('Rahul',   'Sharma',  'rahul@gmail.com',   SHA2('Rahul@123',  256), '9123456780', 200.00),
('Priya',   'Nair',    'priya@gmail.com',   SHA2('Priya@123',  256), '9988776655', 150.00),
('Karthik', 'Reddy',   'karthik@gmail.com', SHA2('Karthik@123',256), '9871234560', 0.00);

-- Vehicles
INSERT INTO vehicle(user_id, reg_number, brand, model, ev_type, bat_cap_kwh, plug_type) VALUES
(1, 'KA01AB1234', 'Tata',  'Nexon EV',    '4-Wheeler', 40.5,  'CCS'),
(1, 'KA02CD5678', 'Ather', '450X',        '2-Wheeler', 2.9,   'AC-Slow'),
(2, 'KA03EF9012', 'Ola',   'S1 Pro',      '2-Wheeler', 3.97,  'AC-Slow'),
(3, 'KA04GH3456', 'MG',    'ZS EV',       '4-Wheeler', 50.3,  'CCS'),
(4, 'KA05IJ7890', 'BYD',   'Atto 3',      '4-Wheeler', 60.48, 'CCS');

-- Sample Bookings
INSERT INTO slot_booking(user_id, vehicle_id, station_id, admin_id, slot_date, start_time, end_time, charger_type, units_consumed, amount, payment_method, tx_id, tx_status, booking_status) VALUES
(1, 1, 1, 1, CURDATE(), '09:00:00', '10:00:00', 'Fast',       15.0, 187.50, 'UPI',    'TXN001', 'Success',  'Completed'),
(2, 3, 2, 1, CURDATE(), '11:00:00', '12:00:00', 'Slow',       2.5,  27.50,  'Wallet', 'TXN002', 'Success',  'Completed'),
(3, 4, 4, 2, CURDATE(), '14:00:00', '16:00:00', 'Ultra-Fast', 30.0, 390.00, 'Card',   'TXN003', 'Success',  'Confirmed'),
(1, 2, 3, 2, CURDATE(), '17:00:00', '18:00:00', 'Slow',       0.0,  0.00,   'UPI',    NULL,     'Pending',  'Pending');

-- =============================================================================
--  VIEWS (for easy reporting)
-- =============================================================================
CREATE VIEW vw_booking_details AS
SELECT
    sb.booking_id,
    CONCAT(u.first_name,' ',u.last_name) AS user_name,
    u.email AS user_email,
    u.phone,
    v.reg_number,
    v.brand,
    v.model,
    cs.station_name,
    cs.city,
    sb.slot_date,
    sb.start_time,
    sb.end_time,
    sb.charger_type,
    sb.units_consumed,
    sb.amount,
    sb.payment_method,
    sb.tx_status,
    sb.booking_status,
    sb.booked_at
FROM slot_booking sb
JOIN user              u   ON sb.user_id    = u.user_id
JOIN vehicle           v   ON sb.vehicle_id = v.vehicle_id
JOIN charging_station  cs  ON sb.station_id = cs.station_id;

CREATE VIEW vw_station_summary AS
SELECT
    cs.station_id,
    cs.station_name,
    cs.city,
    cs.total_slots AS available_slots,
    cs.price_per_unit,
    cs.status,
    COUNT(sb.booking_id)            AS total_bookings,
    SUM(sb.amount)                  AS total_revenue,
    a.name                          AS managed_by
FROM charging_station cs
LEFT JOIN slot_booking sb ON cs.station_id = sb.station_id
                          AND sb.booking_status != 'Cancelled'
JOIN admin a ON cs.admin_id = a.admin_id
GROUP BY cs.station_id, cs.station_name, cs.city,
         cs.total_slots, cs.price_per_unit, cs.status, a.name;

-- =============================================================================
--  END OF SCHEMA
-- =============================================================================
SELECT 'EV Charging DB Setup Complete!' AS status;

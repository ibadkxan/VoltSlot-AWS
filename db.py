"""MySQL connection management for local MySQL and Amazon RDS."""
import os
import mysql.connector
from flask import g


def _db_config():
    config = {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "ev_charging_db"),
        "autocommit": False,
        "charset": "utf8mb4",
        "connection_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
    }
    ssl_ca = os.getenv("DB_SSL_CA")
    if ssl_ca:
        config["ssl_ca"] = ssl_ca
        config["ssl_verify_cert"] = True
    return config


def get_db():
    if "db" not in g:
        g.db = mysql.connector.connect(**_db_config())
    return g.db


def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None and db.is_connected():
        db.close()

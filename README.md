# VoltSlot — EV Charging Slot Booking System

VoltSlot is a Flask + MySQL web application for EV charging station discovery and charging-slot booking. This repository is prepared for local development and deployment to AWS App Runner, Amazon RDS for MySQL, Amazon S3, Amazon SNS and Amazon CloudWatch.

## Features

- Customer registration, login and session management
- Admin login and dashboard
- Charging station listing and station administration
- EV vehicle registration
- Charging-slot availability checks and booking
- Booking cancellation and administration status updates
- Wallet balance and transaction history
- MySQL triggers, views, indexes and relational constraints
- Optional Amazon SNS notifications for booking confirmation and cancellation
- S3-hosted static brand assets
- Health endpoints for deployment checks

## Architecture

```text
Browser
  |
  v
AWS App Runner (Flask + Gunicorn)
  |                 \
  |                  +---- Amazon SNS ---- Email subscribers (admin)
  |
  +---- Amazon RDS for MySQL
  |
  +---- Amazon S3 (static assets)
  |
  +---- Amazon CloudWatch (App Runner logs and metrics)
```

The Flask application currently renders the existing HTML interface and serves the API from the same App Runner service. S3 is used for public static assets; it does not execute Flask or host the MySQL database.

## Repository structure

```text
.
├── app.py
├── db.py
├── services/
│   └── notifications.py
├── templates/
│   └── index.html
├── static/
│   └── voltslot-mark.svg
├── scripts/
│   └── upload_static_to_s3.py
├── docs/
├── ev_charging_schema.sql
├── requirements.txt
├── Procfile
├── apprunner.yaml
├── .env.example
└── .gitignore
```

## Local development

### 1. Requirements

- Python 3.11 recommended
- MySQL 8.0 compatible server
- Git

### 2. Create a virtual environment

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your local MySQL credentials. Never commit `.env` to GitHub.

### 4. Create the database

Create a new empty MySQL database environment, then execute the schema once:

```bash
mysql -u root -p < ev_charging_schema.sql
```

The schema creates `ev_charging_db`, its seven tables, triggers, views, indexes and demo records. It no longer drops an existing database. Run it only against a new/empty database because the table and sample-data statements are not intended to be run repeatedly.

### 5. Start the application

```bash
python app.py
```

Open http://127.0.0.1:5000.

Health checks:

- `GET /health` — application process check
- `GET /health/db` — database connectivity check

## Demo credentials

| Role | Email | Password |
|---|---|---|
| Customer | anish@gmail.com | Anish@123 |
| Customer | rahul@gmail.com | Rahul@123 |
| Super admin | admin@evcharge.in | Admin@123 |
| Station admin | manager@evcharge.in | Mgr@456 |

These are demonstration accounts only. Change or remove them before a public production deployment. Existing sample SHA-256 password hashes are verified for backward compatibility and upgraded to Werkzeug's password hash format after successful login. New registrations use Werkzeug password hashing.

## AWS deployment overview

See [docs/AWS_DEPLOYMENT_GUIDE.md](docs/AWS_DEPLOYMENT_GUIDE.md) for the staged AWS setup:

1. Create an RDS MySQL database and load the schema.
2. Create an S3 bucket and upload `static/` with the supplied script.
3. Create an SNS topic and confirm administrator email subscriptions.
4. Create an App Runner service connected to this GitHub repository.
5. Configure App Runner environment variables and its instance role.
6. Configure private database connectivity and security groups.
7. Verify CloudWatch logs and test booking notifications.

## SNS notification behavior

Set `SNS_BOOKING_TOPIC_ARN` and `AWS_REGION` in the App Runner service environment. Attach an App Runner instance role that allows `sns:Publish` to that one topic. Subscribe administrator email addresses to the SNS topic and click the confirmation link sent by AWS. Booking confirmations and cancellations publish event details to the topic. If SNS is not configured or a publish fails, the booking itself remains saved; the failure is logged rather than rolling back a valid booking.

## Security notes

- Store secrets in App Runner environment variables or AWS Secrets Manager, not in source code.
- Use a long random `SECRET_KEY` and set `COOKIE_SECURE=true` for the HTTPS deployment.
- Keep the RDS database private and allow inbound MySQL only from the App Runner VPC connector security group.
- Do not commit `.env`, database credentials, AWS access keys or private certificates.
- Use an IAM instance role for App Runner; do not place long-lived AWS keys in environment variables.
- Demo payment and wallet flows are academic simulations; they do not process real payments.

## Presentation-day workflow

The deployed App Runner URL is the primary demo entry point. On the presentation laptop, open the URL in a browser; no local Python or MySQL installation is needed for the live demonstration. Keep this GitHub repository and a local setup as a fallback.

### Local fallback with Docker Desktop

After cloning the repository, install Docker Desktop and run:

```bash
docker compose up --build
```

Open http://localhost:5000. The first start initializes MySQL with the schema and sample records. To reset the demo database completely, run `docker compose down -v` and start again (this deletes the local Docker database volume). If code changes are made, push them to GitHub and let App Runner redeploy from the connected branch.

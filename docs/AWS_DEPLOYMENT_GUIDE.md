# VoltSlot — AWS Deployment Guide

This guide deploys the existing Flask application without managing EC2 instances.

## Target services

- **AWS App Runner:** runs Flask + Gunicorn.
- **Amazon RDS for MySQL:** stores application data.
- **Amazon S3:** stores public static assets such as the VoltSlot mark.
- **Amazon SNS:** publishes booking-confirmation and cancellation events to confirmed administrator email subscriptions.
- **Amazon CloudWatch:** receives App Runner application logs and service metrics.
- **AWS IAM:** grants App Runner permission to publish to the SNS topic.

## Important network design decision

The recommended secure design is to keep RDS private and connect App Runner to the database through an App Runner VPC connector. The RDS security group should allow TCP 3306 only from the VPC connector security group.

When App Runner is configured with a VPC connector, its outbound traffic uses the VPC. If the application must reach public internet endpoints from that VPC, a NAT Gateway can add ongoing cost. For this project, avoid adding a NAT Gateway by using AWS service VPC endpoints where appropriate (SNS interface endpoint; S3 gateway endpoint if runtime access to S3 is needed). App Runner's managed log delivery to CloudWatch does not require application code to call CloudWatch APIs.

The exact VPC/subnet/security-group setup should be completed and tested before creating the production App Runner service. Keep the RDS instance private; do not open port 3306 to `0.0.0.0/0`.

## Stage 1 — Prepare GitHub

1. Create a GitHub repository, for example `voltslot-aws`.
2. Push this project root to the repository's default branch.
3. Confirm `.env` is not tracked (`git status --ignored`).
4. In App Runner, connect the GitHub account and select this repository and branch.

## Stage 2 — Create Amazon RDS for MySQL

1. Open RDS in `ap-south-1` (Mumbai).
2. Create a MySQL database using a small development-appropriate instance and storage configuration.
3. Set an administrator username and a strong password. Store them securely.
4. Set initial database name to `ev_charging_db` if the console offers it; otherwise create it after connecting.
5. Prefer **not publicly accessible**.
6. Configure a VPC security group with inbound MySQL TCP 3306 from the App Runner VPC connector security group only.
7. Wait until the database is Available and record its endpoint, port, username and password.
8. From a trusted SQL client that can reach the DB, run `ev_charging_schema.sql` once against the new database. The schema contains demo data and should only be loaded into a new/empty database.
9. If enabling RDS TLS verification, download the current AWS RDS CA bundle and set `DB_SSL_CA` to its path in the runtime image; otherwise follow the account's current RDS TLS guidance. Never disable certificate verification as a permanent workaround.

## Stage 3 — Create S3 bucket for static assets

1. Create a uniquely named S3 bucket in `ap-south-1`.
2. Keep Block Public Access enabled unless you deliberately choose a public-read design for the static assets.
3. Upload the assets:

   ```bash
   pip install boto3
   aws configure
   python scripts/upload_static_to_s3.py --bucket YOUR_UNIQUE_BUCKET_NAME --region ap-south-1
   ```

4. For the simple S3 URL used by this academic demo, if you choose direct public asset access, configure a narrowly scoped bucket policy for `s3:GetObject` on the static asset prefix only. Keep all other prefixes private and never enable public listing. For a production design, keep the bucket private and use CloudFront Origin Access Control. Do not make the whole bucket public.

   Example policy (replace the bucket name; restrict to the static prefix):

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Sid": "PublicReadStaticAssetsOnly",
       "Effect": "Allow",
       "Principal": "*",
       "Action": "s3:GetObject",
       "Resource": "arn:aws:s3:::YOUR_BUCKET_NAME/static/*"
     }]
   }
   ```

   Only objects under `static/` are public. Keep any private uploads outside that prefix and use a separate bucket or signed URLs for private content.
5. Set App Runner `ASSET_BASE_URL` to the bucket's asset base URL (or the CloudFront distribution URL).

S3 is only serving static assets here. The Flask HTML and API continue to run on App Runner.

## Stage 4 — Create SNS topic

1. Open Amazon SNS in `ap-south-1`.
2. Create a Standard topic named `voltslot-booking-alerts`.
3. Create an Email subscription for the administrator email address.
4. Open the subscription-confirmation email and confirm it. The subscription must show Confirmed.
5. Copy the topic ARN. This is the value for `SNS_BOOKING_TOPIC_ARN`.

The topic is intended for administrator alerts. Subscribers receive all events published to this topic, so do not subscribe ordinary customers to this shared admin topic.

## Stage 5 — Create App Runner instance role

1. In IAM, create an App Runner instance role using the AWS service role use case for App Runner instances (trusted service principal `tasks.apprunner.amazonaws.com`).
2. Attach a least-privilege inline policy that allows `sns:Publish` only to the VoltSlot topic ARN:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": "sns:Publish",
         "Resource": "arn:aws:sns:ap-south-1:ACCOUNT_ID:voltslot-booking-alerts"
       }
     ]
   }
   ```

3. If the application later reads/writes S3 at runtime, add only the necessary S3 actions to the exact bucket/prefix. The current upload script runs from your development machine, so the App Runner role does not need S3 write access for that step.

## Stage 6 — Configure App Runner

1. Create an App Runner service from source code.
2. Connect GitHub and select the VoltSlot repository and deployment branch.
3. Use the repository's `apprunner.yaml` (Python 3.11 runtime).
4. Configure the App Runner instance role created in Stage 5.
5. Add environment variables:

   - `APP_ENV` — `production`
   - `SECRET_KEY` — long random value
   - `COOKIE_SECURE` — `true`
   - `DB_HOST` — RDS endpoint (hostname only)
   - `DB_PORT` — `3306`
   - `DB_NAME` — `ev_charging_db`
   - `DB_USER` — RDS username
   - `DB_PASSWORD` — RDS password
   - `AWS_REGION` — `ap-south-1`
   - `SNS_BOOKING_TOPIC_ARN` — SNS topic ARN
   - `ASSET_BASE_URL` — S3 or CloudFront asset URL
6. Configure the App Runner VPC connector for private RDS access, using subnets in the same VPC and its dedicated security group.
7. Confirm the RDS security group allows TCP 3306 from that connector security group only.
8. Deploy and wait for service status to become Running.
9. Test `https://YOUR-APP-RUNNER-URL/health` and `/health/db`.

If using a VPC connector, ensure the SNS interface endpoint is configured in the VPC with private DNS enabled (or provide another controlled network route). Without a route to SNS, the booking will still be stored, but notification publishing will fail and be logged.

## Stage 7 — Verify and demonstrate

1. Open the App Runner URL in a browser.
2. Sign in with the demo customer account.
3. Add/select a vehicle and create a test booking.
4. Verify the booking appears in the customer booking history and admin dashboard.
5. Verify the SNS administrator email arrives.
6. Cancel a test booking and verify the cancellation email.
7. Open App Runner logs in CloudWatch and show the service startup and SNS publish result.
8. Show RDS tables and S3 static asset object in their respective consoles.

## Cost and cleanup

- Create an AWS Budget alert before provisioning resources.
- App Runner, RDS, NAT Gateway and interface VPC endpoints can incur charges while provisioned.
- Avoid NAT Gateway if not required; review the App Runner VPC connector networking design carefully.
- Stop/delete development resources when not demonstrating. RDS stopped instances may restart automatically after the supported stop period; delete only after exporting data if it is no longer needed.
- Remove public access and test data before any real-world use.

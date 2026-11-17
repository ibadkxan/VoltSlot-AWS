"""Amazon SNS booking notifications.

SNS email subscriptions are confirmed by each recipient in their inbox.
The application publishes booking events to a configured SNS topic; the
subscribers (typically station administrators) receive those notifications.
"""
import json
import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _sns_client():
    import boto3
    return boto3.client("sns", region_name=os.getenv("AWS_REGION", "ap-south-1"))


def publish_booking_event(event_type, **details):
    topic_arn = os.getenv("SNS_BOOKING_TOPIC_ARN", "").strip()
    if not topic_arn:
        logger.info("SNS is not configured; skipped %s notification", event_type)
        return False

    subject = f"VoltSlot: {event_type.replace('_', ' ').title()}"
    message = {
        "application": "VoltSlot",
        "event": event_type,
        "details": details,
    }
    try:
        response = _sns_client().publish(
            TopicArn=topic_arn,
            Subject=subject[:100],
            Message=json.dumps(message, indent=2, default=str),
        )
        logger.info("Published SNS event %s (message id %s)", event_type, response.get("MessageId"))
        return True
    except Exception:
        # Booking persistence must not fail just because email notification fails.
        logger.exception("Unable to publish SNS event %s", event_type)
        return False

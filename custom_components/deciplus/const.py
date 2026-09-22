"""Constants for the Xplor Deciplus integration."""

from datetime import timedelta

DOMAIN = "deciplus"

CONF_ZONE_ID = "zone_id"
CONF_ZONE_NAME = "zone_name"
CONF_MEMBER_ID = "member_id"

# options
CONF_SCAN_MINUTES = "scan_minutes"
CONF_DAYS_AHEAD = "days_ahead"
DEFAULT_SCAN_MINUTES = 10
DEFAULT_DAYS_AHEAD = 21  # bookable_at = start - 14 d; 21 shows next week's openings

API_BASE = "https://api.deciplus.pro"
MEMBERS = "/members/v1"
PUBLIC = "/public/v1"
GLOBAL = "/deciplus-members/v1"
HEADERS = {
    "Accept": "application/json",
    "Deciplus-Client-Type": "web_app",
    "Origin": "https://member-app.deciplus.pro",
}
MEMBER_APP = "https://member-app.deciplus.pro"

OPENING_DURATION = timedelta(minutes=15)
OPENING_REFRESH_DELAY = timedelta(seconds=5)  # refresh right after a booking window opens

EVENT_SESSION_AVAILABLE = "deciplus_session_available"
EVENT_BOOKING_CONFIRMED = "deciplus_booking_confirmed"
EVENT_BOOKING_CANCELLED = "deciplus_booking_cancelled"
EVENT_WAITING_POSITION_CHANGED = "deciplus_waiting_position_changed"

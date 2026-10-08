"""Constants for the Librus APIX integration."""

from datetime import timedelta

DOMAIN = "librus_apix"
DEFAULT_NAME = "Librus"

# Configuration keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Update intervals
SCAN_INTERVAL = timedelta(hours=2)
# Jak dlugo pomijamy modul, do ktorego konto nie ma dostepu (np. wylaczone Uwagi)
BRAK_DOSTEPU_CZAS = timedelta(hours=24)

# Default values
DEFAULT_MESSAGES_COUNT = 10
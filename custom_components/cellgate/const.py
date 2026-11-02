"""Mobile API constants. No web-portal transport is used."""

DOMAIN = "cellgate"
MOBILE_URL = "https://cellgate.zapopen.com"
API_PREFIX = "/api/CellCam"
# Mobile request compatibility values documented by the reference integration.
APP_VERSION = "17"
APP_TYPE = "android"

CONF_ENABLE_CALLS = "enable_calls"
DEFAULT_STUN_SERVER = ("stun.l.google.com", 19302)

CONF_BACKEND_URL = "backend_url"
CONF_BACKEND_TOKEN = "backend_token"

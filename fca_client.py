"""FCA Register API client for querying firms and their permissions."""

import time
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://register.fca.org.uk/services/V0.1"
RATE_LIMIT_DELAY = 1.0  # seconds between requests to stay under rate limit


class FCAClient:
    """Client for the FCA Financial Services Register API."""

    def __init__(self, email: str, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Accept": "application/json",
        })
        self._last_request_time = 0.0

    def _rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
        """Make a rate-limited GET request."""
        self._rate_limit()
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429:
                logger.warning("Rate limited, waiting 10 seconds...")
                time.sleep(10)
                return self._get(url, params)
            logger.error("HTTP error for %s: %s", url, e)
            return None
        except requests.exceptions.RequestException as e:
            logger.error("Request error for %s: %s", url, e)
            return None

    def search_firms(self, query: str, page: int = 1) -> Optional[dict]:
        """Search for firms by name.

        Args:
            query: Search term (name or partial name).
            page: Page number for pagination.

        Returns:
            API response dict with 'Data' and 'ResultInfo' keys.
        """
        url = f"{BASE_URL}/Search"
        params = {"q": query, "type": "firm", "page": str(page)}
        return self._get(url, params)

    def get_firm(self, frn: str) -> Optional[dict]:
        """Get firm details by FRN (Firm Reference Number)."""
        url = f"{BASE_URL}/Firm/{frn}"
        return self._get(url)

    def get_firm_permissions(self, frn: str) -> Optional[dict]:
        """Get the regulated permissions/activities for a firm."""
        url = f"{BASE_URL}/Firm/{frn}/Permissions"
        return self._get(url)

    def get_firm_addresses(self, frn: str) -> Optional[dict]:
        """Get firm address information."""
        url = f"{BASE_URL}/Firm/{frn}/Address"
        return self._get(url)

    def get_firm_names(self, frn: str) -> Optional[dict]:
        """Get alternative/trading names for a firm."""
        url = f"{BASE_URL}/Firm/{frn}/Names"
        return self._get(url)

    def get_firm_individuals(self, frn: str) -> Optional[dict]:
        """Get individuals associated with a firm."""
        url = f"{BASE_URL}/Firm/{frn}/Individuals"
        return self._get(url)

    def get_firm_requirements(self, frn: str) -> Optional[dict]:
        """Get regulatory requirements for a firm."""
        url = f"{BASE_URL}/Firm/{frn}/Requirements"
        return self._get(url)

    def get_firm_appointed_reps(self, frn: str) -> Optional[dict]:
        """Get appointed representatives for a firm."""
        url = f"{BASE_URL}/Firm/{frn}/AR"
        return self._get(url)

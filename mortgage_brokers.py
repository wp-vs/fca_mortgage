"""Extract mortgage broker data from the FCA Register.

This script searches the FCA Financial Services Register for firms with
mortgage-related permissions and exports the results to CSV.

Usage:
    python mortgage_brokers.py [--output FILE] [--search-terms TERMS...]

The FCA API does not support filtering by permission type directly,
so the approach is:
  1. Search for firms using mortgage-related terms
  2. Retrieve each firm's permissions
  3. Filter for firms that hold mortgage-related regulated activities
  4. Collect firm details and export to CSV
"""

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from fca_client import FCAClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Mortgage-related regulated activity keywords to match in permissions
MORTGAGE_ACTIVITY_KEYWORDS = [
    "regulated mortgage contract",
    "home finance",
    "mortgage intermediary",
    "mortgage lending",
    "mortgage administration",
    "home purchase plan",
    "equity release",
    "regulated sale and rent back",
]

# Default search terms to find mortgage-related firms
DEFAULT_SEARCH_TERMS = [
    "mortgage",
    "mortgage broker",
    "mortgage adviser",
    "mortgage advisor",
    "home finance",
    "mortgage solutions",
    "mortgage services",
    "equity release",
]

CSV_HEADERS = [
    "FRN",
    "Firm Name",
    "Status",
    "Type",
    "Address",
    "Postcode",
    "Phone",
    "Website",
    "Mortgage Permissions",
    "All Permissions Count",
]


def is_mortgage_permission(permission_text: str) -> bool:
    """Check if a permission description relates to mortgage activities."""
    text_lower = permission_text.lower()
    return any(kw in text_lower for kw in MORTGAGE_ACTIVITY_KEYWORDS)


def extract_mortgage_permissions(permissions_data: dict) -> list[str]:
    """Extract mortgage-related permissions from API response."""
    mortgage_perms = []
    data = permissions_data.get("Data", [])
    for perm in data:
        invest_type = perm.get("Investment Type", "")
        regulated_activity = perm.get("Regulated Activity", "")
        combined = f"{regulated_activity} - {invest_type}".strip(" -")
        if is_mortgage_permission(combined):
            status = perm.get("Status", "")
            if status.lower() in ("authorised", "effective", ""):
                mortgage_perms.append(combined)
    return mortgage_perms


def extract_firm_info(firm_data: dict) -> dict:
    """Extract key fields from firm details response."""
    data = firm_data.get("Data", [{}])
    if isinstance(data, list) and data:
        firm = data[0]
    elif isinstance(data, dict):
        firm = data
    else:
        return {}

    return {
        "FRN": firm.get("FRN", firm.get("Firm Reference Number", "")),
        "Firm Name": firm.get("Organisation Name", firm.get("Name", "")),
        "Status": firm.get("Status", ""),
        "Type": firm.get("Type", firm.get("Type of business or Individual", "")),
    }


def extract_address(address_data: dict) -> dict:
    """Extract address fields from firm address response."""
    data = address_data.get("Data", [{}])
    if isinstance(data, list) and data:
        # Find the current/principal address
        addr = data[0]
        for a in data:
            if a.get("Type", "").lower() in ("principal", "registered", "head office"):
                addr = a
                break
    elif isinstance(data, dict):
        addr = data
    else:
        return {"Address": "", "Postcode": "", "Phone": "", "Website": ""}

    parts = [
        addr.get("Address Line 1", ""),
        addr.get("Address Line 2", ""),
        addr.get("Address Line 3", ""),
        addr.get("Address Line 4", ""),
        addr.get("Town", ""),
        addr.get("County", ""),
        addr.get("Country", ""),
    ]
    address = ", ".join(p for p in parts if p)

    return {
        "Address": address,
        "Postcode": addr.get("Postcode", addr.get("Post Code", "")),
        "Phone": addr.get("Phone Number", addr.get("Phone", "")),
        "Website": addr.get("Website", ""),
    }


def search_and_collect(client: FCAClient, search_terms: list[str]) -> dict[str, dict]:
    """Search for firms and collect unique FRNs."""
    firms_by_frn = {}

    for term in search_terms:
        logger.info("Searching for: '%s'", term)
        page = 1
        while True:
            result = client.search_firms(term, page=page)
            if not result or "Data" not in result:
                logger.warning("No results for '%s' page %d", term, page)
                break

            data = result["Data"]
            if not data:
                break

            for item in data:
                frn = item.get("FRN", item.get("Reference Number", ""))
                if frn and frn not in firms_by_frn:
                    firms_by_frn[frn] = {
                        "Firm Name": item.get("Name", item.get("Organisation Name", "")),
                        "Status": item.get("Status", ""),
                        "Type": item.get("Type of business or Individual", item.get("Type", "")),
                    }

            # Check pagination
            result_info = result.get("ResultInfo", {})
            total = int(result_info.get("total_count", "0"))
            per_page = int(result_info.get("per_page", "20"))
            if page * per_page >= total:
                break
            page += 1

        logger.info("Found %d unique firms so far", len(firms_by_frn))

    return firms_by_frn


def enrich_firm(client: FCAClient, frn: str, basic_info: dict) -> dict | None:
    """Fetch permissions and address for a firm; return enriched row or None."""
    # Get permissions
    perms_data = client.get_firm_permissions(frn)
    if not perms_data:
        return None

    mortgage_perms = extract_mortgage_permissions(perms_data)
    if not mortgage_perms:
        return None  # Skip firms without mortgage permissions

    total_perms = len(perms_data.get("Data", []))

    # Get address
    addr_data = client.get_firm_addresses(frn)
    addr_info = extract_address(addr_data) if addr_data else {
        "Address": "", "Postcode": "", "Phone": "", "Website": ""
    }

    return {
        "FRN": frn,
        "Firm Name": basic_info.get("Firm Name", ""),
        "Status": basic_info.get("Status", ""),
        "Type": basic_info.get("Type", ""),
        "Address": addr_info["Address"],
        "Postcode": addr_info["Postcode"],
        "Phone": addr_info["Phone"],
        "Website": addr_info["Website"],
        "Mortgage Permissions": "; ".join(mortgage_perms),
        "All Permissions Count": total_perms,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract mortgage broker data from the FCA Register"
    )
    parser.add_argument(
        "--output", "-o",
        default="output/mortgage_brokers.csv",
        help="Output CSV file path (default: output/mortgage_brokers.csv)",
    )
    parser.add_argument(
        "--search-terms",
        nargs="+",
        default=None,
        help="Custom search terms (default: built-in mortgage terms)",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Also save results as JSON",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of firms to process (0 = no limit, useful for testing)",
    )
    args = parser.parse_args()

    email = os.getenv("FCA_API_EMAIL")
    api_key = os.getenv("FCA_API_KEY")

    if not email or not api_key:
        logger.error(
            "Missing FCA API credentials. Set FCA_API_EMAIL and FCA_API_KEY in .env file.\n"
            "Register for free at: https://register.fca.org.uk/Developer/s/"
        )
        sys.exit(1)

    client = FCAClient(email, api_key)
    search_terms = args.search_terms or DEFAULT_SEARCH_TERMS

    # Step 1: Search for firms
    logger.info("Step 1: Searching for firms using %d search terms...", len(search_terms))
    firms_by_frn = search_and_collect(client, search_terms)
    logger.info("Found %d unique firms from search", len(firms_by_frn))

    if not firms_by_frn:
        logger.warning("No firms found. Check your API credentials and search terms.")
        sys.exit(0)

    # Step 2: Enrich each firm with permissions and address
    logger.info("Step 2: Checking permissions for each firm...")
    output_dir = Path(args.output).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    mortgage_brokers = []
    processed = 0
    total = len(firms_by_frn) if args.limit == 0 else min(args.limit, len(firms_by_frn))

    for frn, basic_info in firms_by_frn.items():
        if args.limit and processed >= args.limit:
            break

        processed += 1
        if processed % 50 == 0:
            logger.info("Progress: %d/%d firms processed, %d mortgage brokers found",
                        processed, total, len(mortgage_brokers))

        enriched = enrich_firm(client, frn, basic_info)
        if enriched:
            mortgage_brokers.append(enriched)
            logger.debug("Found mortgage broker: %s (%s)", enriched["Firm Name"], frn)

    logger.info("Found %d mortgage brokers out of %d firms checked", len(mortgage_brokers), processed)

    # Step 3: Write CSV
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(mortgage_brokers)
    logger.info("CSV saved to: %s", args.output)

    # Optional: Write JSON
    if args.json_output:
        json_path = args.output.replace(".csv", ".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(mortgage_brokers, f, indent=2, ensure_ascii=False)
        logger.info("JSON saved to: %s", json_path)

    # Summary
    print(f"\n{'='*60}")
    print(f"FCA Mortgage Broker Extraction Complete")
    print(f"{'='*60}")
    print(f"Firms searched:      {processed}")
    print(f"Mortgage brokers:    {len(mortgage_brokers)}")
    print(f"Output file:         {args.output}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

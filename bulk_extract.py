"""Bulk extraction of all mortgage brokers from the FCA Register.

Systematically searches the FCA API to discover all registered firms,
checks each firm's permissions for mortgage-related activities, and
exports matching firms to CSV.

Since the API has no "list all firms" endpoint, we enumerate firms by
searching single characters (a-z, 0-9) and paginating through all results.
This approach captures the vast majority of registered firms.

Progress is checkpointed to disk so the extraction can be resumed if
interrupted. A full run takes several hours due to API rate limits.

Usage:
    python bulk_extract.py [--output FILE] [--checkpoint-dir DIR] [--resume]
                           [--rate-limit RATE] [--skip-search]
"""

import argparse
import csv
import json
import logging
import os
import string
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from fca_client import FCAClient
from mortgage_brokers import (
    CSV_HEADERS,
    extract_address,
    extract_mortgage_permissions,
    is_mortgage_permission,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Search queries designed to cover all firms in the register.
# Single characters catch most firms; two-letter combos and digits fill gaps.
SEARCH_QUERIES = (
    list(string.ascii_lowercase)
    + [str(d) for d in range(10)]
    + ["ltd", "limited", "plc", "llp", "inc", "group", "partners", "associates"]
)

CHECKPOINT_FILENAME = "discovered_frns.json"
PROGRESS_FILENAME = "checked_frns.json"
RESULTS_FILENAME = "mortgage_brokers_partial.json"


def load_json(path: Path) -> dict | list:
    """Load a JSON file, returning empty dict/list if missing."""
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path: Path, data):
    """Atomically save JSON data to file."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.rename(path)


def discover_all_frns(
    client: FCAClient,
    checkpoint_dir: Path,
    queries: list[str] | None = None,
) -> dict[str, dict]:
    """Search the API systematically to discover all firm FRNs.

    Args:
        client: FCA API client.
        checkpoint_dir: Directory for checkpoint files.
        queries: Search queries to use (default: SEARCH_QUERIES).

    Returns:
        Dict mapping FRN -> basic firm info from search results.
    """
    queries = queries or SEARCH_QUERIES
    checkpoint_path = checkpoint_dir / CHECKPOINT_FILENAME
    state = load_json(checkpoint_path)

    # State tracks: discovered firms and which queries are completed
    firms = state.get("firms", {})
    completed_queries = set(state.get("completed_queries", []))

    remaining = [q for q in queries if q not in completed_queries]
    if not remaining:
        logger.info("All %d search queries already completed. %d FRNs discovered.",
                     len(queries), len(firms))
        return firms

    logger.info("Discovering firms: %d queries remaining, %d FRNs found so far",
                len(remaining), len(firms))

    for i, query in enumerate(remaining):
        logger.info("[%d/%d] Searching: '%s' (%d FRNs so far)",
                    i + 1, len(remaining), query, len(firms))

        page = 1
        while True:
            result = client.search_firms(query, page=page)
            if not result or "Data" not in result:
                break

            data = result["Data"]
            if not data:
                break

            for item in data:
                frn = item.get("FRN", item.get("Reference Number", ""))
                if frn and frn not in firms:
                    firms[frn] = {
                        "Firm Name": item.get("Name", item.get("Organisation Name", "")),
                        "Status": item.get("Status", ""),
                        "Type": item.get("Type of business or Individual",
                                         item.get("Type", "")),
                    }

            result_info = result.get("ResultInfo", {})
            total = int(result_info.get("total_count", "0"))
            per_page = int(result_info.get("per_page", "20"))
            if page * per_page >= total:
                break
            page += 1

        completed_queries.add(query)

        # Checkpoint every query
        save_json(checkpoint_path, {
            "firms": firms,
            "completed_queries": list(completed_queries),
        })

    logger.info("Discovery complete: %d unique FRNs found", len(firms))
    return firms


def check_permissions_bulk(
    client: FCAClient,
    firms: dict[str, dict],
    checkpoint_dir: Path,
) -> list[dict]:
    """Check permissions for all firms and collect mortgage brokers.

    Progress is checkpointed so this can resume after interruption.
    """
    progress_path = checkpoint_dir / PROGRESS_FILENAME
    results_path = checkpoint_dir / RESULTS_FILENAME

    checked = set(load_json(progress_path).get("checked", []))
    mortgage_brokers = load_json(results_path) if results_path.exists() else []
    # Index existing results for dedup
    existing_frns = {b["FRN"] for b in mortgage_brokers}

    remaining = {frn: info for frn, info in firms.items() if frn not in checked}
    total = len(firms)
    already_done = total - len(remaining)

    if not remaining:
        logger.info("All %d firms already checked. %d mortgage brokers found.",
                     total, len(mortgage_brokers))
        return mortgage_brokers

    logger.info("Checking permissions: %d remaining out of %d total, "
                "%d mortgage brokers found so far",
                len(remaining), total, len(mortgage_brokers))

    count = 0
    for frn, basic_info in remaining.items():
        count += 1
        done_total = already_done + count

        if done_total % 100 == 0:
            logger.info("Progress: %d/%d firms checked, %d mortgage brokers found",
                        done_total, total, len(mortgage_brokers))

        # Skip non-authorised firms early
        status = basic_info.get("Status", "").lower()
        if status in ("no longer authorised", "terminated", "cancelled"):
            checked.add(frn)
            if count % 500 == 0:
                _save_progress(checkpoint_dir, checked, mortgage_brokers)
            continue

        # Fetch permissions
        perms_data = client.get_firm_permissions(frn)
        if not perms_data:
            checked.add(frn)
            continue

        mortgage_perms = extract_mortgage_permissions(perms_data)
        if mortgage_perms and frn not in existing_frns:
            # Count total permissions
            perm_data = perms_data.get("Data", [])
            if isinstance(perm_data, list):
                total_perms = len(perm_data)
            elif isinstance(perm_data, dict):
                total_perms = len(perm_data)
            else:
                total_perms = 0

            # Fetch address for matching firms
            addr_data = client.get_firm_addresses(frn)
            addr_info = extract_address(addr_data) if addr_data else {
                "Address": "", "Postcode": "", "Phone": "", "Website": ""
            }

            broker = {
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
            mortgage_brokers.append(broker)
            existing_frns.add(frn)
            logger.debug("Found mortgage broker: %s (%s)", broker["Firm Name"], frn)

        checked.add(frn)

        # Checkpoint every 200 firms
        if count % 200 == 0:
            _save_progress(checkpoint_dir, checked, mortgage_brokers)

    # Final save
    _save_progress(checkpoint_dir, checked, mortgage_brokers)
    logger.info("Permission check complete: %d mortgage brokers found", len(mortgage_brokers))
    return mortgage_brokers


def _save_progress(checkpoint_dir: Path, checked: set, mortgage_brokers: list):
    """Save checkpoint files for progress and partial results."""
    save_json(checkpoint_dir / PROGRESS_FILENAME, {"checked": list(checked)})
    save_json(checkpoint_dir / RESULTS_FILENAME, mortgage_brokers)
    logger.debug("Checkpoint saved: %d checked, %d brokers", len(checked), len(mortgage_brokers))


def write_csv(brokers: list[dict], output_path: str):
    """Write final results to CSV."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(brokers)


def main():
    parser = argparse.ArgumentParser(
        description="Bulk extract all mortgage brokers from the FCA Register"
    )
    parser.add_argument(
        "--output", "-o",
        default="output/mortgage_brokers_bulk.csv",
        help="Output CSV file path (default: output/mortgage_brokers_bulk.csv)",
    )
    parser.add_argument(
        "--checkpoint-dir",
        default="output/checkpoints",
        help="Directory for checkpoint files (default: output/checkpoints)",
    )
    parser.add_argument(
        "--skip-search",
        action="store_true",
        help="Skip discovery phase, use existing checkpoint of discovered FRNs",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Also save results as JSON",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=0.25,
        help="Seconds between API requests (default: 0.25, i.e. 4 req/s)",
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

    # Override the default rate limit
    import fca_client
    fca_client.RATE_LIMIT_DELAY = args.rate_limit
    client._rate_limit_delay = args.rate_limit

    checkpoint_dir = Path(args.checkpoint_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()

    # Phase 1: Discover all FRNs
    if args.skip_search:
        checkpoint_path = checkpoint_dir / CHECKPOINT_FILENAME
        state = load_json(checkpoint_path)
        firms = state.get("firms", {})
        if not firms:
            logger.error("No checkpoint found at %s. Run without --skip-search first.",
                         checkpoint_path)
            sys.exit(1)
        logger.info("Loaded %d FRNs from checkpoint", len(firms))
    else:
        logger.info("Phase 1: Discovering all firms via systematic search...")
        firms = discover_all_frns(client, checkpoint_dir)

    # Phase 2: Check permissions for each firm
    logger.info("Phase 2: Checking permissions for %d firms...", len(firms))
    mortgage_brokers = check_permissions_bulk(client, firms, checkpoint_dir)

    # Phase 3: Write output
    write_csv(mortgage_brokers, args.output)
    logger.info("CSV saved to: %s", args.output)

    if args.json_output:
        json_path = args.output.replace(".csv", ".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(mortgage_brokers, f, indent=2, ensure_ascii=False)
        logger.info("JSON saved to: %s", json_path)

    elapsed = time.time() - start_time
    hours, remainder = divmod(int(elapsed), 3600)
    minutes, seconds = divmod(remainder, 60)

    print(f"\n{'='*60}")
    print("FCA Mortgage Broker Bulk Extraction Complete")
    print(f"{'='*60}")
    print(f"Total firms discovered:  {len(firms)}")
    print(f"Mortgage brokers found:  {len(mortgage_brokers)}")
    print(f"Time elapsed:            {hours}h {minutes}m {seconds}s")
    print(f"Output file:             {args.output}")
    print(f"Checkpoints:             {checkpoint_dir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

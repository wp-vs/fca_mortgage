# FCA Mortgage Brokers Extractor

Extracts mortgage broker data from the [FCA Financial Services Register](https://register.fca.org.uk/s/) API.

## How It Works

The FCA Register API doesn't support filtering firms by permission type directly. This tool:

1. **Searches** for firms using mortgage-related terms (e.g., "mortgage broker", "home finance")
2. **Checks permissions** for each firm to identify mortgage-regulated activities
3. **Enriches** matching firms with address and contact details
4. **Exports** results to CSV (and optionally JSON)

### Mortgage-related activities detected:
- Arranging/advising on regulated mortgage contracts
- Home finance activities
- Equity release
- Home purchase plans
- Mortgage lending/administration

## Setup

### 1. Get FCA API credentials (free)

Register at: https://register.fca.org.uk/Developer/s/

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your email and API key
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

## Usage

### Basic run
```bash
python mortgage_brokers.py
```

### Custom output file
```bash
python mortgage_brokers.py --output results/brokers.csv
```

### With JSON output
```bash
python mortgage_brokers.py --json-output
```

### Test with a small sample
```bash
python mortgage_brokers.py --limit 20
```

### Custom search terms
```bash
python mortgage_brokers.py --search-terms "mortgage" "home loan" "remortgage"
```

## Output

The CSV includes these columns:

| Column | Description |
|--------|-------------|
| FRN | Firm Reference Number |
| Firm Name | Registered name |
| Status | Authorisation status |
| Type | Type of business |
| Address | Registered address |
| Postcode | Postal code |
| Phone | Contact number |
| Website | Firm website |
| Mortgage Permissions | Specific mortgage-related regulated activities |
| All Permissions Count | Total number of permissions held |

## API Rate Limits

The FCA API is rate-limited to approximately 10 requests per 10 seconds. The client enforces a 1-second delay between requests. Processing large result sets will take time.

## Project Structure

```
fca_mortgage/
├── fca_client.py         # FCA Register API client
├── mortgage_brokers.py   # Main extraction script
├── requirements.txt      # Python dependencies
├── .env.example          # Credential template
└── .gitignore
```

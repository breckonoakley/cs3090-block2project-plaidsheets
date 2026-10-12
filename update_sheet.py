import os
import time
import argparse
import datetime
from collections import defaultdict
from dotenv import load_dotenv
import plaid
from plaid.api import plaid_api
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()


def build_plaid_client():
    env = os.getenv("PLAID_ENV", "sandbox").lower()
    host = plaid.Environment.Sandbox if env == "sandbox" else plaid.Environment.Production
    config = plaid.Configuration(
        host=host,
        api_key={
            "clientId": os.getenv("PLAID_CLIENT_ID"),
            "secret": os.getenv("PLAID_SECRET"),
        },
    )
    return plaid_api.PlaidApi(plaid.ApiClient(config))


def get_transactions_page(client, request, retries=5):
    # A freshly linked Item returns PRODUCT_NOT_READY until Plaid finishes its first pull
    for attempt in range(retries):
        try:
            return client.transactions_get(request)
        except plaid.ApiException as e:
            if "PRODUCT_NOT_READY" not in str(e.body) or attempt == retries - 1:
                raise
            print("Transactions not ready yet, retrying in 5s...")
            time.sleep(5)


def fetch_transactions(client, access_token, start_date, end_date):
    transactions, offset = [], 0
    while True:
        resp = get_transactions_page(
            client,
            TransactionsGetRequest(
                access_token=access_token,
                start_date=start_date,
                end_date=end_date,
                options=TransactionsGetRequestOptions(offset=offset),
            ),
        )
        transactions.extend(t.to_dict() for t in resp["transactions"])
        if len(transactions) >= resp["total_transactions"]:
            break
        offset = len(transactions)
    return transactions


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
EXCLUDED_CATEGORIES = {"Transfer In", "Transfer Out"}


def get_google_creds():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"), SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return creds


def get_or_create_worksheet(sheet_id, tab_name):
    # Opening by ID/URL needs only the Sheets scope; opening by name would require Drive access
    gc = gspread.authorize(get_google_creds())
    sh = gc.open_by_url(sheet_id) if sheet_id.startswith("http") else gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(tab_name)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=1000, cols=8)
    return ws


def to_row(t):
    """Flatten a Plaid transaction into [Date, Merchant, Category, Amount]."""
    pfc = t.get("personal_finance_category")
    category = pfc["primary"].replace("_", " ").title() if pfc else "Uncategorized"
    merchant = t.get("merchant_name") or t["name"]
    return [t["date"].isoformat(), merchant, category, t["amount"]]


def summarize_by_category(rows):
    """
    Build the spending summary shown next to the transactions.

    rows: list of [date, merchant, category, amount] from to_row().
          Plaid amounts: positive = money out, negative = money in (income, refunds).
    Returns: list of [category, total] rows, e.g. [["Food And Drink", 212.40], ...]
    """
    totals = defaultdict(float)
    for _, _, category, amount in rows:
        # Spending only: skip income/refunds and transfers between your own accounts
        if amount > 0 and category not in EXCLUDED_CATEGORIES:
            totals[category] += amount

    summary = [[c, round(t, 2)] for c, t in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)]
    return summary + [["Total", round(sum(totals.values()), 2)]]


def month_range(month):
    """'YYYY-MM' (or None for the current month) -> (start, end, tab name)."""
    today = datetime.date.today()
    start = datetime.datetime.strptime(month, "%Y-%m").date() if month else today.replace(day=1)
    next_month = (start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    end = min(next_month - datetime.timedelta(days=1), today)
    return start, end, start.strftime("%B %Y")  # e.g. "June 2026"


def main():
    parser = argparse.ArgumentParser(description="Write a month of Plaid transactions to Google Sheets.")
    parser.add_argument("--month", help="month to export as YYYY-MM (default: current month)")
    start, end, tab = month_range(parser.parse_args().month)

    print(f"Fetching transactions from {start} to {end}...")
    client = build_plaid_client()
    txns = fetch_transactions(client, os.getenv("PLAID_ACCESS_TOKEN"), start, end)

    rows = sorted((to_row(t) for t in txns), key=lambda r: r[0])
    summary = summarize_by_category(rows)

    print(f"Writing {len(rows)} transactions to '{tab}' tab...")
    ws = get_or_create_worksheet(os.environ["GOOGLE_SHEET_ID"], tab)
    ws.update(values=[["Date", "Merchant", "Category", "Amount"]] + rows,
              range_name="A1", value_input_option="USER_ENTERED")
    ws.update(values=[["Category", "Total"]] + summary,
              range_name="F1", value_input_option="USER_ENTERED")
    ws.format("A1:G1", {"textFormat": {"bold": True}})

    print("Done.")


if __name__ == "__main__":
    main()

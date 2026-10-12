import os
import threading
import webbrowser
from dotenv import load_dotenv, set_key
from flask import Flask, request, jsonify
import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest

load_dotenv()

app = Flask(__name__)
done = threading.Event()

HTML = """<!DOCTYPE html>
<html>
<head><title>Connect Bank</title></head>
<body>
  <h2>Connect Your Bank Account</h2>
  <button id="btn">Open Plaid Link</button>
  <p id="msg"></p>
  <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
  <script>
  (async () => {
    const { link_token } = await fetch('/api/link_token', { method: 'POST' }).then(r => r.json());
    const handler = Plaid.create({
      token: link_token,
      onSuccess: async (public_token) => {
        document.getElementById('msg').textContent = 'Saving credentials...';
        await fetch('/api/exchange', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ public_token }),
        });
        document.getElementById('msg').textContent = 'Done! You can close this tab and press Ctrl+C in the terminal.';
      },
      onExit: (err) => { if (err) console.error(err); },
    });
    document.getElementById('btn').onclick = () => handler.open();
  })();
  </script>
</body>
</html>"""


def build_client():
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


@app.route("/")
def index():
    return HTML


@app.route("/api/link_token", methods=["POST"])
def link_token():
    client = build_client()
    resp = client.link_token_create(
        LinkTokenCreateRequest(
            products=[Products("transactions")],
            client_name="Finance Tracker",
            country_codes=[CountryCode("US")],
            language="en",
            user=LinkTokenCreateRequestUser(client_user_id="user"),
        )
    )
    return jsonify({"link_token": resp["link_token"]})


@app.route("/api/exchange", methods=["POST"])
def exchange():
    client = build_client()
    resp = client.item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=request.json["public_token"])
    )
    set_key(".env", "PLAID_ACCESS_TOKEN", resp["access_token"])
    print("\nAccess token saved to .env — press Ctrl+C to exit.")
    done.set()
    return jsonify({"ok": True})


if __name__ == "__main__":
    # Port 5000 is taken by AirPlay Receiver on macOS 12+
    threading.Thread(target=lambda: app.run(port=5001), daemon=True).start()
    webbrowser.open("http://localhost:5001")
    done.wait()

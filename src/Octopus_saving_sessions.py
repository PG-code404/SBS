import os
import requests
import json
from dotenv import load_dotenv
from datetime import datetime, timezone
from config.config import OCTOPUS_GRAPHQL_URL

load_dotenv()

OCTOPUS_API_KEY = os.getenv("OCTOPUS_API_KEY")
OCTOPUS_ACCOUNT_NUMBER = os.getenv("OCTOPUS_ACCOUNT_NUMBER")
#OCTOPUS_GRAPHQL_URL = "https://api.octopus.energy/v1/graphql/"

def get_kraken_token():
    """Obtain Kraken JWT token using Octopus API key."""
    query = """
    mutation obtainKrakenToken($input: ObtainJSONWebTokenInput!) {
      obtainKrakenToken(input: $input) { token }
    }
    """
    variables = {"input": {"APIKey": OCTOPUS_API_KEY}}
    response = requests.post(
        OCTOPUS_GRAPHQL_URL,
        json={"query": query, "variables": variables},
        timeout=10
    )
    response.raise_for_status()
    data = response.json()
    token = data["data"]["obtainKrakenToken"]["token"]
    return token


def get_saving_sessions(kraken_token):
    """Fetch all saving sessions for the account using Kraken token."""
    query = """
    query SavingSessions($accountNumber: String) {
      savingSessions(accountNumber: $accountNumber) {
        events {
          id
          code
          startAt
          endAt
          rewardPerKwhInOctoPoints
          status
        }
        eventCount
      }
    }
    """
    variables = {"accountNumber": OCTOPUS_ACCOUNT_NUMBER}
    headers = {"Authorization": f"JWT {kraken_token}"}

    response = requests.post(
        OCTOPUS_GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers=headers,
        timeout=10
    )
    response.raise_for_status()
    data = response.json()["data"]["savingSessions"]["events"]

    # Filter only UPCOMING events
    ongoing = [event for event in data if event["status"] == "ONGOING"]

    # Convert startAt/endAt to datetime objects
    for e in ongoing:
        e["startAt_dt"] = datetime.fromisoformat(e["startAt"].replace("Z", "+00:00"))
        e["endAt_dt"] = datetime.fromisoformat(e["endAt"].replace("Z", "+00:00"))

    return ongoing


def is_in_saving_session(schedule_start, schedule_end, ongoing_session):
    """
    Check if a schedule (datetime objects in UTC) overlaps any upcoming saving session.
    """
    for session in ongoing_session:
        if schedule_start < session["endAt_dt"] and schedule_end > session["startAt_dt"]:
            return True
    return False


# Example usage
if __name__ == "__main__":
    token = get_kraken_token()
    ongoing_session = get_saving_sessions(token)

    # Example schedule
    schedule_start = datetime(2025, 11, 5, 17, 15, tzinfo=timezone.utc)
    schedule_end = datetime(2025, 11, 5, 17, 45, tzinfo=timezone.utc)

    if is_in_saving_session(schedule_start, schedule_end, ongoing_session):
        print("❌ Schedule overlaps saving session — cancel it.")
    else:
        print("✅ Schedule is clear.")

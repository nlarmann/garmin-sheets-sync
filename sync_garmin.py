import os
import json
import gspread
from google.oauth2.service_account import Credentials
from garminconnect import Garmin
from datetime import datetime, timedelta

# --- Config & Environment Variables ---
GARMIN_EMAIL = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD = os.environ["GARMIN_PASSWORD"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
SERVICE_ACCOUNT_INFO = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])

def format_duration(seconds):
    if not seconds:
        return "00:00:00"
    return str(timedelta(seconds=int(seconds)))

def format_pace(distance_meters, duration_seconds):
    if not distance_meters or not duration_seconds or distance_meters == 0:
        return "-"
    miles = distance_meters / 1609.344
    pace_seconds = duration_seconds / miles
    mins = int(pace_seconds // 60)
    secs = int(pace_seconds % 60)
    return f"{mins}:{secs:02d}"

def main():
    # 1. Connect to Google Sheets
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=scopes)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).sheet1

    # Fetch existing Activity IDs to prevent duplicates
    existing_ids = set(sheet.col_values(1)[1:])  # Skip header

    # 2. Connect to Garmin
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()

    # Pull the last 20 activities
    activities = client.get_activities(0, 20)
    new_rows = []

    for act in reversed(activities):  # chronological order
        act_id = str(act.get("activityId"))
        if act_id in existing_ids:
            continue

        act_type = act.get("activityType", {}).get("typeKey", "unknown")
        name = act.get("activityName", "")
        start_time = act.get("startTimeLocal", "")
        dist_meters = act.get("distance", 0) or 0
        dist_miles = round(dist_meters / 1609.344, 2)
        duration_sec = act.get("duration", 0) or 0
        duration_formatted = format_duration(duration_sec)
        pace_formatted = format_pace(dist_meters, duration_sec) if "run" in act_type else "-"
        avg_hr = act.get("averageHR", "-")
        max_hr = act.get("maxHR", "-")
        cadence = act.get("averageRunningCadenceInStepsPerMinute", "-")
        elev_gain_meters = act.get("elevationGain", 0) or 0
        elev_gain_ft = round(elev_gain_meters * 3.28084, 0)
        aerobic_te = act.get("aerobicTrainingEffect", "-")
        anaerobic_te = act.get("anaerobicTrainingEffect", "-")

        new_rows.append([
            act_id,
            start_time,
            act_type,
            name,
            dist_miles,
            duration_formatted,
            pace_formatted,
            avg_hr,
            max_hr,
            cadence,
            elev_gain_ft,
            aerobic_te,
            anaerobic_te
        ])

    if new_rows:
        sheet.append_rows(new_rows)
        print(f"Successfully appended {len(new_rows)} new activities.")
    else:
        print("No new activities to append.")

if __name__ == "__main__":
    main()
import os
import json
import requests
import gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from garminconnect import Garmin

# --- Environment Configurations ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")

# Optional Cronometer Credentials
CRONOMETER_EMAIL = os.environ.get("CRONOMETER_EMAIL")
CRONOMETER_PASSWORD = os.environ.get("CRONOMETER_PASSWORD")

# User Targets for Marine Corps Marathon Prep
TARGET_CALORIES = 2510
TARGET_PROTEIN = 165
TARGET_CARBS = 305
TARGET_FAT = 70


def format_seconds_to_hhmm(seconds):
    if not seconds or seconds <= 0:
        return "0:00"
    td = timedelta(seconds=int(seconds))
    total_minutes = int(td.total_seconds() // 60)
    hours = total_minutes // 60
    mins = total_minutes % 60
    return f"{hours}:{mins:02d}"


def format_pace(distance_meters, duration_seconds):
    if not distance_meters or not duration_seconds or distance_meters == 0:
        return "-"
    miles = distance_meters / 1609.344
    pace_seconds = duration_seconds / miles
    mins = int(pace_seconds // 60)
    secs = int(pace_seconds % 60)
    return f"{mins}:{secs:02d}"


# -------------------------------------------------------------
# 1. Garmin Activities Sync
# -------------------------------------------------------------
def sync_activities(client, spreadsheet):
    try:
        sheet = spreadsheet.worksheet("Activities Log")
    except Exception:
        sheet = spreadsheet.sheet1

    existing_ids = set(sheet.col_values(1)[1:])
    activities = client.get_activities(0, 25)
    new_rows = []

    for act in reversed(activities):
        act_id = str(act.get("activityId"))
        if act_id in existing_ids:
            continue

        act_type = act.get("activityType", {}).get("typeKey", "unknown")
        name = act.get("activityName", "")
        start_time = act.get("startTimeLocal", "")
        dist_meters = act.get("distance", 0) or 0
        dist_miles = round(dist_meters / 1609.344, 2)
        duration_sec = act.get("duration", 0) or 0
        duration_formatted = str(timedelta(seconds=int(duration_sec)))
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
            anaerobic_te,
            ""  # Focus / Notes placeholder
        ])

    if new_rows:
        sheet.append_rows(new_rows)
        print(f"Activities: Appended {len(new_rows)} new activities.")
    else:
        print("Activities: No new activities found.")


# -------------------------------------------------------------
# 2. Garmin Health & Recovery Sync (Sleep, HRV, Body Battery, RHR)
# -------------------------------------------------------------
def sync_health_and_recovery(client, spreadsheet):
    try:
        sheet = spreadsheet.worksheet("Daily Health & Recovery")
    except Exception as e:
        print(f"Health: Tab 'Daily Health & Recovery' not found ({e}). Skipping.")
        return

    existing_dates = set(sheet.col_values(1)[1:])
    new_rows = []

    # Check the last 7 days for any missing recovery entries
    today = datetime.now().date()
    for day_offset in range(7, -1, -1):
        target_date = today - timedelta(days=day_offset)
        date_str = target_date.isoformat()

        if date_str in existing_dates:
            continue

        try:
            # 1. Sleep Data
            sleep_data = client.get_sleep_data(date_str) or {}
            daily_sleep = sleep_data.get("dailySleepDTO", {})
            sleep_score = daily_sleep.get("sleepScores", {}).get("overall", {}).get("value", "-")
            total_sleep = format_seconds_to_hhmm(daily_sleep.get("sleepTimeSeconds", 0))
            deep_sleep = format_seconds_to_hhmm(daily_sleep.get("deepSleepSeconds", 0))
            rem_sleep = format_seconds_to_hhmm(daily_sleep.get("remSleepSeconds", 0))
            light_sleep = format_seconds_to_hhmm(daily_sleep.get("lightSleepSeconds", 0))
            awake_sleep = format_seconds_to_hhmm(daily_sleep.get("awakeSleepSeconds", 0))

            # 2. HRV Data
            hrv_data = client.get_hrv_data(date_str) or {}
            hrv_summary = hrv_data.get("hrvSummary", {}) or {}
            hrv_avg = hrv_summary.get("lastNightAvg", "-")
            hrv_status = hrv_summary.get("status", "-")

            # 3. Resting Heart Rate
            rhr_data = client.get_rhr_day(date_str) or {}
            rhr_val = rhr_data.get("restingHeartRate", "-")

            # 4. Body Battery & Stress
            bb_data = client.get_body_battery(date_str) or []
            bb_peak = "-"
            bb_low = "-"
            if bb_data:
                charged_vals = [entry.get("charged", 0) for entry in bb_data if "charged" in entry]
                drained_vals = [entry.get("drained", 0) for entry in bb_data if "drained" in entry]
                if charged_vals:
                    bb_peak = max(charged_vals)
                if drained_vals:
                    bb_low = min(drained_vals)

            stress_data = client.get_daily_stress(date_str) or {}
            avg_stress = stress_data.get("avgStressLevel", "-")

            # 5. Steps & Active Calories
            stats = client.get_stats(date_str) or {}
            steps = stats.get("totalSteps", "-")
            active_cal = stats.get("activeKilocalories", "-")

            new_rows.append([
                date_str,
                sleep_score,
                total_sleep,
                deep_sleep,
                rem_sleep,
                light_sleep,
                awake_sleep,
                hrv_avg,
                hrv_status,
                rhr_val,
                bb_peak,
                bb_low,
                avg_stress,
                steps,
                active_cal,
                ""  # Recovery Notes placeholder
            ])
        except Exception as err:
            print(f"Health: Could not retrieve metrics for {date_str}: {err}")

    if new_rows:
        sheet.append_rows(new_rows)
        print(f"Health: Appended {len(new_rows)} daily recovery records.")
    else:
        print("Health: All recent health records up to date.")


# -------------------------------------------------------------
# 3. Cronometer Nutrition & Weight Sync (Optional)
# -------------------------------------------------------------
def sync_cronometer(spreadsheet):
    if not CRONOMETER_EMAIL or not CRONOMETER_PASSWORD:
        print("Cronometer: Credentials not set. Skipping nutrition sync.")
        return

    try:
        sheet = spreadsheet.worksheet("Nutrition & Weight")
    except Exception as e:
        print(f"Cronometer: Tab 'Nutrition & Weight' not found ({e}). Skipping.")
        return

    existing_dates = set(sheet.col_values(1)[1:])
    session = requests.Session()

    try:
        # Authenticate with Cronometer web API
        login_resp = session.post(
            "https://cronometer.com/login",
            data={"username": CRONOMETER_EMAIL, "password": CRONOMETER_PASSWORD},
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if login_resp.status_code != 200:
            print("Cronometer: Login failed. Check credentials.")
            return

        today = datetime.now().date()
        new_rows = []

        # Pull past 3 days to capture final food logging
        for day_offset in range(3, -1, -1):
            target_date = today - timedelta(days=day_offset)
            date_str = target_date.isoformat()
            if date_str in existing_dates:
                continue

            summary_resp = session.get(
                f"https://cronometer.com/export?type=dailySummary&start={date_str}&end={date_str}",
                headers={"User-Agent": "Mozilla/5.0"}
            )

            if summary_resp.status_code == 200 and summary_resp.text.strip():
                lines = summary_resp.text.strip().split("\n")
                if len(lines) > 1:
                    headers = [h.strip().strip('"') for h in lines[0].split(",")]
                    values = [v.strip().strip('"') for v in lines[1].split(",")]
                    data_dict = dict(zip(headers, values))

                    weight = data_dict.get("Weight (lbs)", data_dict.get("Weight", "-"))
                    consumed_cals = float(data_dict.get("Energy (kcal)", 0) or 0)
                    protein = float(data_dict.get("Protein (g)", 0) or 0)
                    carbs = float(data_dict.get("Net Carbs (g)", data_dict.get("Carbs (g)", 0)) or 0)
                    fat = float(data_dict.get("Fat (g)", 0) or 0)
                    fiber = float(data_dict.get("Fiber (g)", 0) or 0)
                    net_cals = consumed_cals - TARGET_CALORIES

                    new_rows.append([
                        date_str,
                        weight,
                        consumed_cals,
                        TARGET_CALORIES,
                        protein,
                        TARGET_PROTEIN,
                        carbs,
                        TARGET_CARBS,
                        fat,
                        TARGET_FAT,
                        fiber,
                        net_cals,
                        ""  # Notes
                    ])

        if new_rows:
            sheet.append_rows(new_rows)
            print(f"Cronometer: Appended {len(new_rows)} nutrition entries.")
        else:
            print("Cronometer: Nutrition data up to date.")
    except Exception as e:
        print(f"Cronometer: Sync failed: {e}")


# -------------------------------------------------------------
# Main Orchestrator
# -------------------------------------------------------------
def main():
    if not SPREADSHEET_ID or not SERVICE_ACCOUNT_JSON:
        raise ValueError("Missing SPREADSHEET_ID or GCP_SERVICE_ACCOUNT_JSON.")

    # Authorize Google Sheets
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    service_info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(service_info, scopes=scopes)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)

    # Initialize Garmin Client
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()

    # 1. Activities Sync
    sync_activities(client, spreadsheet)

    # 2. Health & Recovery Sync
    sync_health_and_recovery(client, spreadsheet)

    # 3. Cronometer Sync
    sync_cronometer(spreadsheet)


if __name__ == "__main__":
    main()
import os
import json
import csv
from io import StringIO
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
        return "-", None
    miles = distance_meters / 1609.344
    pace_seconds = duration_seconds / miles
    mins = int(pace_seconds // 60)
    secs = int(pace_seconds % 60)
    return f"{mins}:{secs:02d}", pace_seconds


def speed_to_pace(speed_mps):
    """Converts meters per second to min/mile string and seconds per mile."""
    if not speed_mps or speed_mps <= 0.5:
        return "-", None
    pace_sec = 1609.344 / speed_mps
    mins = int(pace_sec // 60)
    secs = int(pace_sec % 60)
    return f"{mins}:{secs:02d}", pace_sec


def classify_workout(act_type, dist_miles, avg_pace_sec, best_pace_sec, avg_hr, max_hr, avg_cadence, max_cadence, zone5_mins):
    """Classifies workout type using interval and intensity signatures."""
    if "run" not in act_type.lower():
        return act_type.replace("_", " ").title()

    if dist_miles >= 10.0:
        return "Long Endurance Run"

    hr_delta = (max_hr - avg_hr) if (isinstance(max_hr, (int, float)) and isinstance(avg_hr, (int, float))) else 0

    # Interval / Sprint Detection Signature:
    # 1. Large HR spike (>= 22 bpm difference between max and avg)
    # 2. Significant time in Zone 5 (>= 0.5 min)
    # 3. High max cadence (>= 185) with low average cadence (<= 150) from walk/rest periods
    # 4. Very fast top speed (< 6:30 min/mi) with moderate/slow average pace (> 9:00 min/mi)
    is_interval = (
        hr_delta >= 22 or
        (isinstance(zone5_mins, (int, float)) and zone5_mins >= 0.5) or
        (isinstance(best_pace_sec, (int, float)) and best_pace_sec < 390 and (avg_pace_sec is None or avg_pace_sec > 540)) or
        (isinstance(max_cadence, (int, float)) and max_cadence >= 185 and isinstance(avg_cadence, (int, float)) and avg_cadence <= 150)
    )

    if is_interval:
        return "Interval / Sprints (VO2 Max)"

    if isinstance(avg_hr, (int, float)) and avg_hr >= 160:
        return "Tempo / Threshold Run"

    if isinstance(avg_hr, (int, float)) and avg_hr < 148:
        return "Easy / Base Aerobic"

    return "Aerobic Endurance Run"


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

        # Paces
        avg_pace_str, avg_pace_sec = format_pace(dist_meters, duration_sec) if "run" in act_type else ("-", None)
        max_speed = act.get("maxSpeed", 0) or 0
        best_pace_str, best_pace_sec = speed_to_pace(max_speed) if "run" in act_type else ("-", None)

        # Heart Rate
        avg_hr = act.get("averageHR", "-")
        max_hr = act.get("maxHR", "-")

        # Cadence
        avg_cadence = act.get("averageRunningCadenceInStepsPerMinute", "-")
        max_cadence = act.get("maxRunningCadenceInStepsPerMinute", "-")

        # Elevation & Training Effect
        elev_gain_meters = act.get("elevationGain", 0) or 0
        elev_gain_ft = round(elev_gain_meters * 3.28084, 0)
        aerobic_te = act.get("aerobicTrainingEffect", "-")
        anaerobic_te = act.get("anaerobicTrainingEffect", "-")

        # Zone 5 Duration
        zone5_mins = "-"
        try:
            hr_zones = client.get_activity_hr_in_timezones(act_id)
            if hr_zones and isinstance(hr_zones, list):
                for z in hr_zones:
                    if z.get("zoneNumber") == 5:
                        zone5_mins = round(z.get("secsInZone", 0) / 60.0, 1)
                        break
        except Exception:
            pass

        # Classification
        classification = classify_workout(
            act_type=act_type,
            dist_miles=dist_miles,
            avg_pace_sec=avg_pace_sec,
            best_pace_sec=best_pace_sec,
            avg_hr=avg_hr if isinstance(avg_hr, (int, float)) else None,
            max_hr=max_hr if isinstance(max_hr, (int, float)) else None,
            avg_cadence=avg_cadence if isinstance(avg_cadence, (int, float)) else None,
            max_cadence=max_cadence if isinstance(max_cadence, (int, float)) else None,
            zone5_mins=zone5_mins if isinstance(zone5_mins, (int, float)) else None
        )

        new_rows.append([
            act_id,
            start_time,
            act_type,
            name,
            dist_miles,
            duration_formatted,
            avg_pace_str,
            best_pace_str,
            avg_hr,
            max_hr,
            avg_cadence,
            max_cadence,
            elev_gain_ft,
            zone5_mins,
            aerobic_te,
            anaerobic_te,
            classification
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

    today = datetime.now().date()
    for day_offset in range(7, -1, -1):
        target_date = today - timedelta(days=day_offset)
        date_str = target_date.isoformat()

        if date_str in existing_dates:
            continue

        sleep_score, total_sleep, deep_sleep, rem_sleep, light_sleep, awake_sleep = "-", "-", "-", "-", "-", "-"
        hrv_avg, hrv_status = "-", "-"
        rhr_val = "-"
        bb_peak, bb_low = "-", "-"
        avg_stress = "-"
        steps, active_cal = "-", "-"

        # 1. Sleep
        try:
            sleep_data = client.get_sleep_data(date_str) or {}
            daily_sleep = sleep_data.get("dailySleepDTO", {}) or {}
            sleep_score = daily_sleep.get("sleepScores", {}).get("overall", {}).get("value", "-")
            total_sleep = format_seconds_to_hhmm(daily_sleep.get("sleepTimeSeconds", 0))
            deep_sleep = format_seconds_to_hhmm(daily_sleep.get("deepSleepSeconds", 0))
            rem_sleep = format_seconds_to_hhmm(daily_sleep.get("remSleepSeconds", 0))
            light_sleep = format_seconds_to_hhmm(daily_sleep.get("lightSleepSeconds", 0))
            awake_sleep = format_seconds_to_hhmm(daily_sleep.get("awakeSleepSeconds", 0))
        except Exception:
            pass

        # 2. HRV
        try:
            hrv_data = client.get_hrv_data(date_str) or {}
            hrv_summary = hrv_data.get("hrvSummary", {}) or {}
            hrv_avg = hrv_summary.get("lastNightAvg", "-")
            hrv_status = hrv_summary.get("status", "-")
        except Exception:
            pass

        # 3. Resting HR
        try:
            rhr_data = client.get_rhr_day(date_str) or {}
            rhr_val = rhr_data.get("restingHeartRate", "-")
        except Exception:
            pass

        # 4. Body Battery & Stress
        try:
            bb_data = client.get_body_battery(date_str) or []
            if isinstance(bb_data, list) and bb_data:
                charged_vals = [e.get("charged", 0) for e in bb_data if isinstance(e, dict) and "charged" in e]
                drained_vals = [e.get("drained", 0) for e in bb_data if isinstance(e, dict) and "drained" in e]
                if charged_vals:
                    bb_peak = max(charged_vals)
                if drained_vals:
                    bb_low = min(drained_vals)
        except Exception:
            pass

        try:
            stress_data = client.get_stress_data(date_str) or client.get_all_day_stress(date_str) or {}
            if isinstance(stress_data, dict):
                avg_stress = stress_data.get("avgStressLevel", stress_data.get("averageStressLevel", "-"))
        except Exception:
            pass

        # 5. Steps & Active Calories
        try:
            stats = client.get_stats(date_str) or {}
            steps = stats.get("totalSteps", "-")
            active_cal = stats.get("activeKilocalories", "-")
        except Exception:
            pass

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
            ""
        ])

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
        print("Cronometer: Credentials not set in env. Skipping nutrition sync.")
        return

    try:
        sheet = spreadsheet.worksheet("Nutrition & Weight")
    except Exception as e:
        print(f"Cronometer: Tab 'Nutrition & Weight' not found ({e}). Skipping.")
        return

    existing_dates = set(sheet.col_values(1)[1:])
    session = requests.Session()
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        login_resp = session.post(
            "https://cronometer.com/login",
            data={"username": CRONOMETER_EMAIL, "password": CRONOMETER_PASSWORD},
            headers=headers,
            timeout=30,
        )
        if login_resp.status_code != 200:
            print(f"Cronometer: Login failed ({login_resp.status_code}). Check credentials or account access.")
            return

        today = datetime.now().date()
        new_rows = []
        requested_dates = 0
        failed_dates = []

        for day_offset in range(3, -1, -1):
            target_date = today - timedelta(days=day_offset)
            date_str = target_date.isoformat()
            if date_str in existing_dates:
                continue
            requested_dates += 1

            summary_resp = session.get(
                f"https://cronometer.com/export?type=dailySummary&start={date_str}&end={date_str}",
                headers=headers,
                timeout=30,
            )

            content_type = summary_resp.headers.get("Content-Type", "").lower()
            if summary_resp.status_code != 200 or "text/html" in content_type:
                failed_dates.append(f"{date_str} (HTTP {summary_resp.status_code})")
                continue

            rows = list(csv.DictReader(StringIO(summary_resp.text)))
            if not rows:
                continue

            data_dict = rows[0]

            def numeric_value(*keys):
                for key in keys:
                    value = (data_dict.get(key) or "").strip()
                    if value and value not in {"-", "--"}:
                        try:
                            return float(value.replace(",", ""))
                        except ValueError:
                            pass
                return 0.0

            weight = data_dict.get("Weight (lbs)", data_dict.get("Weight", "-")) or "-"
            consumed_cals = numeric_value("Energy (kcal)")
            protein = numeric_value("Protein (g)")
            carbs = numeric_value("Net Carbs (g)", "Carbs (g)")
            fat = numeric_value("Fat (g)")
            fiber = numeric_value("Fiber (g)")
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
                ""
            ])

        if new_rows:
            sheet.append_rows(new_rows)
            print(f"Cronometer: Appended {len(new_rows)} nutrition entries.")
        elif failed_dates:
            print(f"Cronometer: Could not fetch nutrition data for {', '.join(failed_dates)}.")
        elif requested_dates:
            print("Cronometer: No nutrition rows returned for the requested dates.")
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

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    service_info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(service_info, scopes=scopes)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)

    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()

    sync_activities(client, spreadsheet)
    sync_health_and_recovery(client, spreadsheet)
    sync_cronometer(spreadsheet)


if __name__ == "__main__":
    main()
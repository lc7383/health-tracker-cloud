"""
health_tracker_cloud.py
------------------------
SHARED version of the health tracker, backed by Supabase (free hosted Postgres)
so data survives Streamlit Cloud restarts and multiple people can use it.

Login is a real username + password, hashed and salted before being stored
in Supabase (never stored in plain text). Each person's data is filtered
by the name they type. This is NOT strong security: anyone who knows or
guesses another person's name could view/edit that person's entries. It's
meant for a small trusted group (family/friends), not a public product.

Dependencies:
    pip install streamlit pandas plotly supabase

Setup:
    1. Create a free Supabase project at supabase.com
    2. Run supabase_schema.sql in the Supabase SQL Editor to create tables
    3. In Streamlit Cloud, add these to your app's Secrets:
        SUPABASE_URL = "https://xxxx.supabase.co"
        SUPABASE_KEY = "your-anon-public-key"
       (Find both under Project Settings -> API in Supabase)
    4. For local testing, create a .streamlit/secrets.toml file with the same keys

Usage:
    streamlit run health_tracker_cloud.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date, datetime, timedelta, timezone
from supabase import create_client
import hashlib
import secrets as secrets_module
import extra_streamlit_components as stx

st.set_page_config(page_title="Health Tracker (Shared)", page_icon="🩺", layout="wide")


# ── Supabase connection ─────────────────────────────────────────────
@st.cache_resource
def get_client():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


try:
    supabase = get_client()
except Exception:
    st.error(
        "Supabase credentials not found. Add SUPABASE_URL and SUPABASE_KEY "
        "to your Streamlit Secrets (see the setup notes at the top of this file)."
    )
    st.stop()


# ── Persistent login (cookie-based, so reopening the app doesn't log you out) ─
cookie_manager = stx.CookieManager(key="health_tracker_cookies")
COOKIE_NAME = "ht_session_token"
SESSION_DAYS = 30


def create_session(name):
    """Generate a long-lived token, store it against the user, and set it as a browser cookie."""
    token = secrets_module.token_hex(32)
    expiry = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    supabase.table("users").update(
        {"session_token": token, "session_expiry": expiry}
    ).eq("user_name", name).execute()
    cookie_manager.set(
        COOKIE_NAME, token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS),
        key="set_session_cookie",
    )


def clear_session(name):
    supabase.table("users").update(
        {"session_token": None, "session_expiry": None}
    ).eq("user_name", name).execute()
    cookie_manager.delete(COOKIE_NAME, key="delete_session_cookie")


def resolve_user_from_cookie():
    token = cookie_manager.get(COOKIE_NAME)
    if not token:
        return None
    resp = supabase.table("users").select("user_name, session_expiry").eq("session_token", token).execute()
    if not resp.data:
        return None
    row = resp.data[0]
    if not row.get("session_expiry"):
        return None
    try:
        expiry = datetime.fromisoformat(row["session_expiry"])
    except ValueError:
        return None
    if datetime.now(timezone.utc) > expiry:
        return None
    return row["user_name"]


# ── Login (username + password) ─────────────────────────────────────
def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


def get_existing_users():
    resp = supabase.table("users").select("user_name").order("user_name").execute()
    return [row["user_name"] for row in resp.data] if resp.data else []


def register_user(name, password):
    salt = secrets_module.token_hex(16)
    pw_hash = hash_password(password, salt)
    supabase.table("users").upsert(
        {"user_name": name, "password_hash": pw_hash, "salt": salt}
    ).execute()


def verify_user(name, password):
    resp = supabase.table("users").select("password_hash, salt").eq("user_name", name).execute()
    if not resp.data:
        return False
    row = resp.data[0]
    if not row["salt"] or not row["password_hash"]:
        return False
    return hash_password(password, row["salt"]) == row["password_hash"]


def needs_password_setup(name):
    resp = supabase.table("users").select("password_hash").eq("user_name", name).execute()
    if not resp.data:
        return False
    return not resp.data[0]["password_hash"]


if "user_name" not in st.session_state:
    st.session_state["user_name"] = ""

# Try to auto-login from a remembered cookie before showing the login screen
if not st.session_state["user_name"]:
    remembered = resolve_user_from_cookie()
    if remembered:
        st.session_state["user_name"] = remembered

if not st.session_state["user_name"]:
    st.title("🩺 Health Tracker")
    st.caption("Log in with your name and password, or create a new account below.")

    existing_users = get_existing_users()
    choice = st.selectbox(
        "Who are you?",
        options=existing_users + ["➕ New user..."],
        index=None,
        placeholder="Select your name",
    )

    if choice == "➕ New user...":
        new_name = st.text_input("Choose a username")
        new_pw = st.text_input("Choose a password", type="password")
        confirm_pw = st.text_input("Confirm password", type="password")
        if st.button("Create account"):
            if not new_name.strip() or not new_pw:
                st.error("Please enter a username and password.")
            elif new_name.strip() in existing_users:
                st.error("That username is already taken. Pick a different one.")
            elif new_pw != confirm_pw:
                st.error("Passwords don't match.")
            else:
                register_user(new_name.strip(), new_pw)
                create_session(new_name.strip())
                st.session_state["user_name"] = new_name.strip()
                st.rerun()
    elif choice:
        if needs_password_setup(choice):
            st.info(f"'{choice}' doesn't have a password yet. Set one now to secure this account.")
            new_pw = st.text_input("New password", type="password")
            confirm_pw = st.text_input("Confirm password", type="password")
            if st.button("Set password"):
                if not new_pw:
                    st.error("Please enter a password.")
                elif new_pw != confirm_pw:
                    st.error("Passwords don't match.")
                else:
                    register_user(choice, new_pw)
                    create_session(choice)
                    st.session_state["user_name"] = choice
                    st.rerun()
        else:
            pw = st.text_input("Password", type="password")
            if st.button("Log in"):
                if verify_user(choice, pw):
                    create_session(choice)
                    st.session_state["user_name"] = choice
                    st.rerun()
                else:
                    st.error("Incorrect password.")
    st.stop()

user = st.session_state["user_name"]


# ── Data helpers ─────────────────────────────────────────────────────
def insert_row(table, row: dict):
    row["user_name"] = user
    supabase.table(table).insert(row).execute()


def read_table(table):
    resp = (
        supabase.table(table)
        .select("*")
        .eq("user_name", user)
        .order("log_date", desc=True)
        .execute()
    )
    return pd.DataFrame(resp.data) if resp.data else pd.DataFrame()


def get_distinct_values(table, column):
    resp = supabase.table(table).select(column).eq("user_name", user).execute()
    if not resp.data:
        return []
    values = {row[column] for row in resp.data if row.get(column)}
    return sorted(values)


def get_last_value(table, match_column, match_value, return_column):
    resp = (
        supabase.table(table)
        .select(return_column)
        .eq("user_name", user)
        .eq(match_column, match_value)
        .order("log_date", desc=True)
        .limit(1)
        .execute()
    )
    if resp.data and resp.data[0].get(return_column) is not None:
        return resp.data[0][return_column]
    return None


def pick_or_add(label, options, key):
    """Dropdown of past values plus an inline 'add new' text field. Returns the chosen/typed string."""
    choice = st.selectbox(label, options=options + ["➕ New..."], index=None,
                          placeholder=f"Select or add {label.lower()}", key=f"{key}_select")
    if choice == "➕ New...":
        return st.text_input(f"New {label.lower()}", key=f"{key}_new")
    return choice or ""


FOOD_MACRO_COLS = ["calories", "protein_g", "fiber_g", "sugar_g", "carbs_g", "fat_g", "sodium_mg"]
FOOD_MACRO_LABELS = {
    "calories": "Calories", "protein_g": "Protein (g)", "fiber_g": "Fiber (g)",
    "sugar_g": "Sugar (g)", "carbs_g": "Carbs (g)", "fat_g": "Fat (g)", "sodium_mg": "Sodium (mg)",
}


def render_food_summary(food_df):
    st.subheader("🍽️ Food Summary")
    if food_df.empty:
        st.caption("No food logged yet.")
        return

    df = food_df.copy()
    df["log_date"] = pd.to_datetime(df["log_date"])
    for col in FOOD_MACRO_COLS:
        if col not in df.columns:
            df[col] = None
        df[col] = pd.to_numeric(df[col], errors="coerce")

    daily = df.groupby(df["log_date"].dt.date)[FOOD_MACRO_COLS].sum(min_count=1).reset_index()
    daily = daily.rename(columns={"log_date": "Date", **FOOD_MACRO_LABELS}).sort_values("Date", ascending=False)

    df["week_start"] = (df["log_date"] - pd.to_timedelta(df["log_date"].dt.weekday, unit="D")).dt.date
    weekly = df.groupby("week_start")[FOOD_MACRO_COLS].sum(min_count=1).reset_index()
    weekly = weekly.rename(columns={"week_start": "Week of", **FOOD_MACRO_LABELS}).sort_values("Week of", ascending=False)

    daily_tab, weekly_tab = st.tabs(["Daily Totals", "Weekly Totals"])
    with daily_tab:
        st.dataframe(daily, use_container_width=True, hide_index=True)
        if len(daily) > 1:
            st.plotly_chart(
                px.bar(daily.sort_values("Date"), x="Date", y="Calories", title="Daily Calories"),
                use_container_width=True,
            )
    with weekly_tab:
        st.dataframe(weekly, use_container_width=True, hide_index=True)
        if len(weekly) > 1:
            st.plotly_chart(
                px.bar(weekly.sort_values("Week of"), x="Week of", y="Calories", title="Weekly Calories"),
                use_container_width=True,
            )


def render_exercise_summary(exercise_df):
    st.subheader("🏃 Exercise Summary")
    if exercise_df.empty:
        st.caption("No exercise logged yet.")
        return

    df = exercise_df.copy()
    df["log_date"] = pd.to_datetime(df["log_date"])
    df["duration_min"] = pd.to_numeric(df["duration_min"], errors="coerce")
    df["activity"] = df["activity"].fillna("(unspecified)")
    df["intensity"] = df["intensity"].fillna("(unspecified)")

    # Overall totals (all activities combined)
    daily_total = (
        df.groupby(df["log_date"].dt.date)["duration_min"]
        .agg(Sessions="count", **{"Total Minutes": "sum"})
        .reset_index()
        .rename(columns={"log_date": "Date"})
        .sort_values("Date", ascending=False)
    )

    df["week_start"] = (df["log_date"] - pd.to_timedelta(df["log_date"].dt.weekday, unit="D")).dt.date
    weekly_total = (
        df.groupby("week_start")["duration_min"]
        .agg(Sessions="count", **{"Total Minutes": "sum"})
        .reset_index()
        .rename(columns={"week_start": "Week of"})
        .sort_values("Week of", ascending=False)
    )

    # Breakdown by activity + intensity
    daily_breakdown = (
        df.groupby([df["log_date"].dt.date, "activity", "intensity"])["duration_min"]
        .agg(Sessions="count", **{"Total Minutes": "sum"})
        .reset_index()
        .rename(columns={"log_date": "Date", "activity": "Activity", "intensity": "Intensity"})
        .sort_values(["Date", "Activity", "Intensity"], ascending=[False, True, True])
    )

    weekly_breakdown = (
        df.groupby(["week_start", "activity", "intensity"])["duration_min"]
        .agg(Sessions="count", **{"Total Minutes": "sum"})
        .reset_index()
        .rename(columns={"week_start": "Week of", "activity": "Activity", "intensity": "Intensity"})
        .sort_values(["Week of", "Activity", "Intensity"], ascending=[False, True, True])
    )

    daily_tab, weekly_tab = st.tabs(["Daily Totals", "Weekly Totals"])
    with daily_tab:
        st.markdown("**Total (all activities):**")
        st.dataframe(daily_total, use_container_width=True, hide_index=True)
        if len(daily_total) > 1:
            st.plotly_chart(
                px.bar(daily_total.sort_values("Date"), x="Date", y="Total Minutes", title="Daily Exercise Minutes"),
                use_container_width=True,
            )
        st.markdown("**Breakdown by activity & intensity:**")
        st.dataframe(daily_breakdown, use_container_width=True, hide_index=True)
    with weekly_tab:
        st.markdown("**Total (all activities):**")
        st.dataframe(weekly_total, use_container_width=True, hide_index=True)
        if len(weekly_total) > 1:
            st.plotly_chart(
                px.bar(weekly_total.sort_values("Week of"), x="Week of", y="Total Minutes", title="Weekly Exercise Minutes"),
                use_container_width=True,
            )
        st.markdown("**Breakdown by activity & intensity:**")
        st.dataframe(weekly_breakdown, use_container_width=True, hide_index=True)


def render_sleep_summary(sleep_df):
    st.subheader("😴 Sleep Summary")
    if sleep_df.empty:
        st.caption("No sleep logged yet.")
        return

    df = sleep_df.copy()
    df["log_date"] = pd.to_datetime(df["log_date"])
    df["hours"] = pd.to_numeric(df["hours"], errors="coerce")
    df["quality"] = pd.to_numeric(df["quality"], errors="coerce")

    daily = (
        df.groupby(df["log_date"].dt.date)
        .agg(Entries=("hours", "count"), **{"Total Hours": ("hours", "sum"), "Avg Quality": ("quality", "mean")})
        .reset_index()
        .rename(columns={"log_date": "Date"})
        .sort_values("Date", ascending=False)
    )
    daily["Avg Quality"] = daily["Avg Quality"].round(1)

    df["week_start"] = (df["log_date"] - pd.to_timedelta(df["log_date"].dt.weekday, unit="D")).dt.date
    weekly = (
        df.groupby("week_start")
        .agg(Nights=("hours", "count"), **{"Total Hours": ("hours", "sum"), "Avg Quality": ("quality", "mean")})
        .reset_index()
        .rename(columns={"week_start": "Week of"})
        .sort_values("Week of", ascending=False)
    )
    weekly["Avg Quality"] = weekly["Avg Quality"].round(1)

    daily_tab, weekly_tab = st.tabs(["Daily Totals", "Weekly Totals"])
    with daily_tab:
        st.dataframe(daily, use_container_width=True, hide_index=True)
    with weekly_tab:
        st.dataframe(weekly, use_container_width=True, hide_index=True)


def render_symptoms_summary(symptoms_df):
    st.subheader("🩹 Symptoms Summary")
    if symptoms_df.empty:
        st.caption("No symptoms logged yet.")
        return

    df = symptoms_df.copy()
    df["log_date"] = pd.to_datetime(df["log_date"])
    df["severity"] = pd.to_numeric(df["severity"], errors="coerce")
    df["symptom"] = df["symptom"].fillna("(unspecified)")

    daily = (
        df.groupby([df["log_date"].dt.date, "symptom"])
        .agg(Occurrences=("symptom", "count"), **{"Avg Severity": ("severity", "mean")})
        .reset_index()
        .rename(columns={"log_date": "Date", "symptom": "Symptom"})
        .sort_values(["Date", "Symptom"], ascending=[False, True])
    )
    daily["Avg Severity"] = daily["Avg Severity"].round(1)

    df["week_start"] = (df["log_date"] - pd.to_timedelta(df["log_date"].dt.weekday, unit="D")).dt.date
    weekly = (
        df.groupby(["week_start", "symptom"])
        .agg(Occurrences=("symptom", "count"), **{"Avg Severity": ("severity", "mean")})
        .reset_index()
        .rename(columns={"week_start": "Week of", "symptom": "Symptom"})
        .sort_values(["Week of", "Symptom"], ascending=[False, True])
    )
    weekly["Avg Severity"] = weekly["Avg Severity"].round(1)

    daily_tab, weekly_tab = st.tabs(["Daily Totals", "Weekly Totals"])
    with daily_tab:
        st.dataframe(daily, use_container_width=True, hide_index=True)
    with weekly_tab:
        st.dataframe(weekly, use_container_width=True, hide_index=True)


def render_self_care_summary(self_care_df):
    st.subheader("🧘 Self-Care Summary")
    if self_care_df.empty:
        st.caption("No self-care logged yet.")
        return

    df = self_care_df.copy()
    df["log_date"] = pd.to_datetime(df["log_date"])
    df["duration_min"] = pd.to_numeric(df["duration_min"], errors="coerce")
    df["activity"] = df["activity"].fillna("(unspecified)")

    daily = (
        df.groupby([df["log_date"].dt.date, "activity"])["duration_min"]
        .agg(Sessions="count", **{"Total Minutes": "sum"})
        .reset_index()
        .rename(columns={"log_date": "Date", "activity": "Activity"})
        .sort_values(["Date", "Activity"], ascending=[False, True])
    )

    df["week_start"] = (df["log_date"] - pd.to_timedelta(df["log_date"].dt.weekday, unit="D")).dt.date
    weekly = (
        df.groupby(["week_start", "activity"])["duration_min"]
        .agg(Sessions="count", **{"Total Minutes": "sum"})
        .reset_index()
        .rename(columns={"week_start": "Week of", "activity": "Activity"})
        .sort_values(["Week of", "Activity"], ascending=[False, True])
    )

    daily_tab, weekly_tab = st.tabs(["Daily Totals", "Weekly Totals"])
    with daily_tab:
        st.dataframe(daily, use_container_width=True, hide_index=True)
    with weekly_tab:
        st.dataframe(weekly, use_container_width=True, hide_index=True)


def delete_row(table, row_id):
    supabase.table(table).delete().eq("id", row_id).eq("user_name", user).execute()


def update_row(table, row_id, values: dict):
    supabase.table(table).update(values).eq("id", row_id).eq("user_name", user).execute()


def bulk_update_food_by_description(description, values: dict):
    """Apply corrected macro values to every past entry with this same description."""
    supabase.table("food").update(values).eq("user_name", user).eq("description", description).execute()


def delete_ui(table, df):
    if df.empty:
        return
    with st.expander("Delete an entry"):
        row_id = st.number_input(f"Row ID to delete ({table})", min_value=0, step=1, key=f"del_{table}")
        if st.button("Delete", key=f"del_btn_{table}"):
            if row_id in df["id"].values:
                delete_row(table, int(row_id))
                st.success(f"Deleted row {row_id}.")
                st.rerun()
            else:
                st.error("That ID isn't in your entries.")


def food_edit_ui(df):
    if df.empty:
        return
    with st.expander("Edit a food entry"):
        row_id = st.number_input("Row ID to edit", min_value=0, step=1, key="edit_food_id")

        # If the selected row changed, clear stale widget values so fields reload fresh
        if st.session_state.get("edit_food_loaded_id") != row_id:
            for k in ["edit_food_meal", "edit_food_desc", "edit_food_cal", "edit_food_protein",
                      "edit_food_fiber", "edit_food_carbs", "edit_food_fat", "edit_food_sugar",
                      "edit_food_sodium", "edit_food_notes"]:
                st.session_state.pop(k, None)
            st.session_state["edit_food_loaded_id"] = row_id

        if row_id in df["id"].values:
            row = df[df["id"] == row_id].iloc[0]

            def cur(col):
                val = row.get(col)
                return float(val) if pd.notna(val) else 0.0

            e_meal = st.selectbox("Meal", ["Breakfast", "Lunch", "Dinner", "Snack"],
                                  index=["Breakfast", "Lunch", "Dinner", "Snack"].index(row["meal"])
                                  if row.get("meal") in ["Breakfast", "Lunch", "Dinner", "Snack"] else 0,
                                  key="edit_food_meal")
            e_desc = st.text_input("Description", value=row.get("description", ""), key="edit_food_desc")
            c1, c2, c3 = st.columns(3)
            with c1:
                e_cal = st.number_input("Calories", min_value=0.0, step=10.0, value=cur("calories"), key="edit_food_cal")
                e_protein = st.number_input("Protein (g)", min_value=0.0, step=1.0, value=cur("protein_g"), key="edit_food_protein")
            with c2:
                e_fiber = st.number_input("Fiber (g)", min_value=0.0, step=1.0, value=cur("fiber_g"), key="edit_food_fiber")
                e_carbs = st.number_input("Carbs (g)", min_value=0.0, step=1.0, value=cur("carbs_g"), key="edit_food_carbs")
            with c3:
                e_fat = st.number_input("Fat (g)", min_value=0.0, step=1.0, value=cur("fat_g"), key="edit_food_fat")
                e_sugar = st.number_input("Sugar (g)", min_value=0.0, step=1.0, value=cur("sugar_g"), key="edit_food_sugar")
            e_sodium = st.number_input("Sodium (mg)", min_value=0.0, step=10.0, value=cur("sodium_mg"), key="edit_food_sodium")
            e_notes = st.text_input("Notes", value=row.get("notes", "") or "", key="edit_food_notes")

            st.caption("This updates the existing entry in place — it will NOT create a new food log entry.")
            if st.button("Update entry", key="update_food_btn"):
                update_row("food", int(row_id), {
                    "meal": e_meal, "description": e_desc,
                    "calories": e_cal or None, "protein_g": e_protein or None,
                    "fiber_g": e_fiber or None, "carbs_g": e_carbs or None,
                    "fat_g": e_fat or None, "sugar_g": e_sugar or None,
                    "sodium_mg": e_sodium or None, "notes": e_notes,
                })
                st.success(f"Updated row {row_id}.")
                for k in ["edit_food_meal", "edit_food_desc", "edit_food_cal", "edit_food_protein",
                          "edit_food_fiber", "edit_food_carbs", "edit_food_fat", "edit_food_sugar",
                          "edit_food_sodium", "edit_food_notes", "edit_food_loaded_id"]:
                    st.session_state.pop(k, None)
                st.rerun()
        elif row_id:
            st.error("That ID isn't in your entries.")


def symptom_edit_ui(df):
    if df.empty:
        return
    with st.expander("Edit a symptom entry (e.g. fill in end time)"):
        row_id = st.number_input("Row ID to edit", min_value=0, step=1, key="edit_sym_id")

        if st.session_state.get("edit_sym_loaded_id") != row_id:
            for k in ["edit_sym_symptom", "edit_sym_severity", "edit_sym_start",
                      "edit_sym_has_ended", "edit_sym_end", "edit_sym_notes"]:
                st.session_state.pop(k, None)
            st.session_state["edit_sym_loaded_id"] = row_id

        if row_id in df["id"].values:
            row = df[df["id"] == row_id].iloc[0]

            def parse_time(val):
                if not val:
                    return datetime.now().time()
                try:
                    return datetime.strptime(str(val)[:5], "%H:%M").time()
                except ValueError:
                    return datetime.now().time()

            e_symptom = st.text_input("Symptom", value=row.get("symptom", ""), key="edit_sym_symptom")
            e_severity = st.slider("Severity (1-5)", 1, 5,
                                   int(row["severity"]) if pd.notna(row.get("severity")) else 3,
                                   key="edit_sym_severity")
            e_start = st.time_input("Start time", value=parse_time(row.get("start_time")), key="edit_sym_start")
            e_has_ended = st.checkbox("Has it ended?", value=bool(row.get("end_time")), key="edit_sym_has_ended")
            e_end = st.time_input("End time", value=parse_time(row.get("end_time")), key="edit_sym_end") if e_has_ended else None
            e_notes = st.text_input("Notes", value=row.get("notes", "") or "", key="edit_sym_notes")

            if st.button("Update entry", key="update_sym_btn"):
                update_row("symptoms", int(row_id), {
                    "symptom": e_symptom, "severity": e_severity,
                    "start_time": e_start.strftime("%H:%M") if e_start else None,
                    "end_time": e_end.strftime("%H:%M") if e_end else None,
                    "notes": e_notes,
                })
                st.success(f"Updated row {row_id}.")
                for k in ["edit_sym_symptom", "edit_sym_severity", "edit_sym_start",
                          "edit_sym_has_ended", "edit_sym_end", "edit_sym_notes", "edit_sym_loaded_id"]:
                    st.session_state.pop(k, None)
                st.rerun()
        elif row_id:
            st.error("That ID isn't in your entries.")


# ── Sidebar ──────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"**Logged in as:** {user}")
    st.caption(f"You'll stay logged in on this device for {SESSION_DAYS} days.")
    if st.button("Switch user"):
        clear_session(user)
        st.session_state["user_name"] = ""
        st.rerun()


# ── App layout ───────────────────────────────────────────────────────
st.title("🩺 Personal Health Tracker (Shared)")

tabs = st.tabs(["📊 Dashboard", "⚖️ Weight", "🏃 Exercise", "😴 Sleep", "💧 Water", "🍽️ Food", "💊 Vitamins", "🩹 Symptoms", "🧘 Self-Care"])

with tabs[0]:
    st.subheader("Last 30 Days")
    weight_df = read_table("weight")
    exercise_df = read_table("exercise")
    sleep_df = read_table("sleep")
    water_df = read_table("water")
    food_df = read_table("food")
    vitamins_df = read_table("vitamins")
    symptoms_df = read_table("symptoms")
    self_care_df = read_table("self_care")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        latest_weight = weight_df["weight_lbs"].iloc[0] if not weight_df.empty else None
        st.metric("Latest Weight", f"{latest_weight:.1f} lbs" if latest_weight else "—")
    with col2:
        total_ex = exercise_df["duration_min"].sum() if not exercise_df.empty else 0
        st.metric("Exercise (total min)", f"{total_ex:.0f}")
    with col3:
        avg_sleep = sleep_df["hours"].mean() if not sleep_df.empty else None
        st.metric("Avg Sleep", f"{avg_sleep:.1f} hrs" if avg_sleep else "—")
    with col4:
        avg_water = water_df["ounces"].mean() if not water_df.empty else None
        st.metric("Avg Water", f"{avg_water:.0f} oz" if avg_water else "—")

    c1, c2 = st.columns(2)
    with c1:
        if not weight_df.empty:
            st.plotly_chart(px.line(weight_df.sort_values("log_date"), x="log_date", y="weight_lbs",
                                     title="Weight Trend", markers=True), use_container_width=True)
    with c2:
        if not water_df.empty:
            st.plotly_chart(px.bar(water_df.sort_values("log_date"), x="log_date", y="ounces",
                                    title="Water by Day"), use_container_width=True)

    if not vitamins_df.empty:
        st.markdown("**Vitamins logged (last 10):**")
        st.dataframe(vitamins_df.head(10), use_container_width=True)

    render_food_summary(food_df)
    render_exercise_summary(exercise_df)
    render_sleep_summary(sleep_df)
    render_symptoms_summary(symptoms_df)
    render_self_care_summary(self_care_df)

with tabs[1]:
    st.subheader("Log Weight / Measurements")
    with st.form("weight_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            w_date = st.date_input("Date", value=date.today())
            w_weight = st.number_input("Weight (lbs)", min_value=0.0, step=0.1)
        with c2:
            w_waist = st.number_input("Waist (in, optional)", min_value=0.0, step=0.1)
            w_notes = st.text_input("Notes")
        if st.form_submit_button("Save"):
            insert_row("weight", {"log_date": str(w_date), "weight_lbs": w_weight,
                                   "waist_in": w_waist or None, "notes": w_notes})
            st.success("Saved.")
            st.rerun()
    df = read_table("weight")
    st.dataframe(df, use_container_width=True)
    delete_ui("weight", df)

with tabs[2]:
    st.subheader("Log Exercise")
    c1, c2 = st.columns(2)
    with c1:
        e_date = st.date_input("Date", value=date.today(), key="e_date")
        e_activity = pick_or_add("Activity", get_distinct_values("exercise", "activity"), "e_activity")
        e_duration = st.number_input("Duration (minutes)", min_value=0.0, step=5.0, key="e_duration")
    with c2:
        e_intensity = st.selectbox("Intensity", ["Low", "Moderate", "High"], key="e_intensity")
        e_notes = st.text_input("Notes", key="e_notes")
    if st.button("Save", key="e_save"):
        if not e_activity:
            st.error("Please select or add an activity.")
        else:
            insert_row("exercise", {"log_date": str(e_date), "activity": e_activity,
                                     "duration_min": e_duration, "intensity": e_intensity, "notes": e_notes})
            st.success("Saved.")
            for k in ["e_activity_select", "e_activity_new", "e_duration", "e_notes"]:
                st.session_state.pop(k, None)
            st.rerun()
    df = read_table("exercise")
    st.dataframe(df, use_container_width=True)
    delete_ui("exercise", df)

with tabs[3]:
    st.subheader("Log Sleep")
    with st.form("sleep_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            s_date = st.date_input("Date", value=date.today(), key="s_date")
            s_hours = st.number_input("Hours slept", min_value=0.0, max_value=24.0, step=0.25)
        with c2:
            s_quality = st.slider("Quality (1-5)", 1, 5, 3)
            s_notes = st.text_input("Notes", key="s_notes")
        if st.form_submit_button("Save"):
            insert_row("sleep", {"log_date": str(s_date), "hours": s_hours,
                                  "quality": s_quality, "notes": s_notes})
            st.success("Saved.")
            st.rerun()
    df = read_table("sleep")
    st.dataframe(df, use_container_width=True)
    delete_ui("sleep", df)

with tabs[4]:
    st.subheader("Log Water Intake")
    with st.form("water_form", clear_on_submit=True):
        w_date2 = st.date_input("Date", value=date.today(), key="water_date")
        w_ounces = st.number_input("Ounces", min_value=0.0, step=1.0)
        if st.form_submit_button("Save"):
            insert_row("water", {"log_date": str(w_date2), "ounces": w_ounces})
            st.success("Saved.")
            st.rerun()
    df = read_table("water")
    st.dataframe(df, use_container_width=True)
    delete_ui("water", df)

with tabs[5]:
    st.subheader("Log Food")
    c1, c2 = st.columns(2)
    with c1:
        f_date = st.date_input("Date", value=date.today(), key="f_date")
        f_meal = st.selectbox("Meal", ["Breakfast", "Lunch", "Dinner", "Snack"], key="f_meal")
    with c2:
        f_description = pick_or_add("Description", get_distinct_values("food", "description"), "f_desc")
        if f_description and st.session_state.get("f_last_desc") != f_description:
            for col, skey in [("calories", "f_calories"), ("protein_g", "f_protein"),
                               ("fiber_g", "f_fiber"), ("sugar_g", "f_sugar"),
                               ("carbs_g", "f_carbs"), ("fat_g", "f_fat"),
                               ("sodium_mg", "f_sodium")]:
                past_val = get_last_value("food", "description", f_description, col)
                if past_val is not None:
                    st.session_state[skey] = float(past_val)
            st.session_state["f_last_desc"] = f_description
        f_calories = st.number_input("Calories (optional)", min_value=0.0, step=10.0, key="f_calories")
    c3, c4, c5 = st.columns(3)
    with c3:
        f_protein = st.number_input("Protein (g, optional)", min_value=0.0, step=1.0, key="f_protein")
        f_carbs = st.number_input("Carbs (g, optional)", min_value=0.0, step=1.0, key="f_carbs")
    with c4:
        f_fiber = st.number_input("Fiber (g, optional)", min_value=0.0, step=1.0, key="f_fiber")
        f_fat = st.number_input("Fat (g, optional)", min_value=0.0, step=1.0, key="f_fat")
    with c5:
        f_sugar = st.number_input("Sugar (g, optional)", min_value=0.0, step=1.0, key="f_sugar")
        f_sodium = st.number_input("Sodium (mg, optional)", min_value=0.0, step=10.0, key="f_sodium")
    f_notes = st.text_input("Notes", key="f_notes")
    f_apply_all = st.checkbox(
        "Also fix all previous entries of this food with these values",
        key="f_apply_all",
        help="Use this if you're correcting a mistake (like wrong calories) — it will update every past entry with this same description, not just today's.",
    )
    if st.button("Save", key="f_save"):
        if not f_description:
            st.error("Please select or add a description.")
        else:
            macro_values = {
                "calories": f_calories or None,
                "protein_g": f_protein or None,
                "fiber_g": f_fiber or None,
                "sugar_g": f_sugar or None,
                "carbs_g": f_carbs or None,
                "fat_g": f_fat or None,
                "sodium_mg": f_sodium or None,
            }
            insert_row("food", {"log_date": str(f_date), "meal": f_meal,
                                 "description": f_description, "notes": f_notes, **macro_values})
            if f_apply_all:
                bulk_update_food_by_description(f_description, macro_values)
                st.success("Saved, and updated all previous entries for this food too.")
            else:
                st.success("Saved.")
            for k in ["f_desc_select", "f_desc_new", "f_calories", "f_protein", "f_fiber",
                      "f_sugar", "f_carbs", "f_fat", "f_sodium", "f_notes", "f_last_desc", "f_apply_all"]:
                st.session_state.pop(k, None)
            st.rerun()
    df = read_table("food")
    st.dataframe(df, use_container_width=True)
    food_edit_ui(df)
    delete_ui("food", df)

with tabs[6]:
    st.subheader("Log Vitamins / Supplements")
    c1, c2 = st.columns(2)
    with c1:
        v_date = st.date_input("Date", value=date.today(), key="v_date")
        v_name = pick_or_add("Vitamin / Supplement name", get_distinct_values("vitamins", "vitamin_name"), "v_name")
    with c2:
        v_dose = pick_or_add("Dose", get_distinct_values("vitamins", "dose"), "v_dose")
        v_taken = st.checkbox("Taken", value=True, key="v_taken")
    v_notes = st.text_input("Notes", key="v_notes")
    if st.button("Save", key="v_save"):
        if not v_name:
            st.error("Please select or add a vitamin/supplement name.")
        else:
            insert_row("vitamins", {"log_date": str(v_date), "vitamin_name": v_name,
                                     "dose": v_dose, "taken": v_taken, "notes": v_notes})
            st.success("Saved.")
            for k in ["v_name_select", "v_name_new", "v_dose_select", "v_dose_new", "v_notes"]:
                st.session_state.pop(k, None)
            st.rerun()
    df = read_table("vitamins")
    st.dataframe(df, use_container_width=True)
    delete_ui("vitamins", df)

with tabs[7]:
    st.subheader("Log a Symptom")
    c1, c2 = st.columns(2)
    with c1:
        s_date = st.date_input("Date", value=date.today(), key="sym_date")
        s_symptom = pick_or_add("Symptom", get_distinct_values("symptoms", "symptom"), "sym_name")
        s_start = st.time_input("Start time", value=datetime.now().time(), key="sym_start")
    with c2:
        s_severity = st.slider("Severity (1-5)", 1, 5, 3, key="sym_severity")
        s_has_ended = st.checkbox("Has it ended already?", key="sym_has_ended")
        s_end = st.time_input("End time", value=datetime.now().time(), key="sym_end") if s_has_ended else None
    s_notes = st.text_input("Notes (e.g. what triggered it)", key="sym_notes")
    if st.button("Save", key="sym_save"):
        if not s_symptom:
            st.error("Please select or add a symptom.")
        else:
            insert_row("symptoms", {"log_date": str(s_date), "symptom": s_symptom,
                                     "severity": s_severity,
                                     "start_time": s_start.strftime("%H:%M") if s_start else None,
                                     "end_time": s_end.strftime("%H:%M") if s_end else None,
                                     "notes": s_notes})
            st.success("Saved.")
            for k in ["sym_name_select", "sym_name_new", "sym_severity", "sym_notes",
                      "sym_start", "sym_end", "sym_has_ended"]:
                st.session_state.pop(k, None)
            st.rerun()

    df = read_table("symptoms")
    st.dataframe(df, use_container_width=True)
    symptom_edit_ui(df)
    delete_ui("symptoms", df)

with tabs[8]:
    st.subheader("Log Self-Care")
    c1, c2 = st.columns(2)
    with c1:
        sc_date = st.date_input("Date", value=date.today(), key="sc_date")
        sc_activity = pick_or_add("Activity (e.g. Massage, Meditation, Spa Day)",
                                   get_distinct_values("self_care", "activity"), "sc_activity")
    with c2:
        sc_duration = st.number_input("Duration (minutes, optional)", min_value=0.0, step=5.0, key="sc_duration")
    sc_notes = st.text_input("Notes", key="sc_notes")
    if st.button("Save", key="sc_save"):
        if not sc_activity:
            st.error("Please select or add an activity.")
        else:
            insert_row("self_care", {"log_date": str(sc_date), "activity": sc_activity,
                                      "duration_min": sc_duration or None, "notes": sc_notes})
            st.success("Saved.")
            for k in ["sc_activity_select", "sc_activity_new", "sc_duration", "sc_notes"]:
                st.session_state.pop(k, None)
            st.rerun()

    df = read_table("self_care")
    st.dataframe(df, use_container_width=True)
    delete_ui("self_care", df)

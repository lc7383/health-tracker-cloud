"""
health_tracker_cloud.py
------------------------
SHARED version of the health tracker, backed by Supabase (free hosted Postgres)
so data survives Streamlit Cloud restarts and multiple people can use it.

Login is a simple name entry (no password) - each person's data is filtered
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
from datetime import date
from supabase import create_client

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


# ── Login (pick existing name, or add a new one — no password) ─────
def get_existing_users():
    resp = supabase.table("users").select("user_name").order("user_name").execute()
    return [row["user_name"] for row in resp.data] if resp.data else []


def register_user(name):
    # upsert so re-selecting an existing name never errors
    supabase.table("users").upsert({"user_name": name}).execute()


if "user_name" not in st.session_state:
    st.session_state["user_name"] = ""

if not st.session_state["user_name"]:
    st.title("🩺 Health Tracker")
    st.caption(
        "⚠️ This is a shared app for a small trusted group. There's no password — "
        "anyone who knows your name could see your entries. Use a nickname if you'd prefer."
    )

    existing_users = get_existing_users()
    choice = st.selectbox(
        "Who are you?",
        options=existing_users + ["➕ New user..."],
        index=None,
        placeholder="Select your name",
    )

    chosen_name = None
    if choice == "➕ New user...":
        new_name = st.text_input("Enter your name")
        if st.button("Continue") and new_name.strip():
            chosen_name = new_name.strip()
    elif choice:
        if st.button("Continue"):
            chosen_name = choice

    if chosen_name:
        register_user(chosen_name)
        st.session_state["user_name"] = chosen_name
        st.rerun()
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


def delete_row(table, row_id):
    supabase.table(table).delete().eq("id", row_id).eq("user_name", user).execute()


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


# ── Sidebar ──────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"**Logged in as:** {user}")
    if st.button("Switch user"):
        st.session_state["user_name"] = ""
        st.rerun()


# ── App layout ───────────────────────────────────────────────────────
st.title("🩺 Personal Health Tracker (Shared)")

tabs = st.tabs(["📊 Dashboard", "⚖️ Weight", "🏃 Exercise", "😴 Sleep", "💧 Water", "🍽️ Food", "💊 Vitamins"])

with tabs[0]:
    st.subheader("Last 30 Days")
    weight_df = read_table("weight")
    exercise_df = read_table("exercise")
    sleep_df = read_table("sleep")
    water_df = read_table("water")
    food_df = read_table("food")
    vitamins_df = read_table("vitamins")

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
        if not exercise_df.empty:
            st.plotly_chart(px.bar(exercise_df, x="log_date", y="duration_min", color="activity",
                                    title="Exercise by Activity Type"), use_container_width=True)
    with c2:
        if not sleep_df.empty:
            st.plotly_chart(px.bar(sleep_df.sort_values("log_date"), x="log_date", y="hours",
                                    title="Sleep by Day"), use_container_width=True)
        if not water_df.empty:
            st.plotly_chart(px.bar(water_df.sort_values("log_date"), x="log_date", y="ounces",
                                    title="Water by Day"), use_container_width=True)

    if not vitamins_df.empty:
        st.markdown("**Vitamins logged (last 10):**")
        st.dataframe(vitamins_df.head(10), use_container_width=True)

    if not food_df.empty:
        st.markdown("**Food logged (last 10):**")
        st.dataframe(food_df.head(10), use_container_width=True)

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
    with st.form("exercise_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            e_date = st.date_input("Date", value=date.today(), key="e_date")
            e_activity = st.text_input("Activity (e.g. Running, Yoga, Weights)")
            e_duration = st.number_input("Duration (minutes)", min_value=0.0, step=5.0)
        with c2:
            e_intensity = st.selectbox("Intensity", ["Low", "Moderate", "High"])
            e_notes = st.text_input("Notes", key="e_notes")
        if st.form_submit_button("Save"):
            insert_row("exercise", {"log_date": str(e_date), "activity": e_activity,
                                     "duration_min": e_duration, "intensity": e_intensity, "notes": e_notes})
            st.success("Saved.")
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
    with st.form("food_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            f_date = st.date_input("Date", value=date.today(), key="f_date")
            f_meal = st.selectbox("Meal", ["Breakfast", "Lunch", "Dinner", "Snack"])
        with c2:
            f_description = st.text_input("What did you eat?")
            f_calories = st.number_input("Calories (optional)", min_value=0.0, step=10.0)
        f_notes = st.text_input("Notes", key="f_notes")
        if st.form_submit_button("Save"):
            insert_row("food", {"log_date": str(f_date), "meal": f_meal,
                                 "description": f_description,
                                 "calories": f_calories or None, "notes": f_notes})
            st.success("Saved.")
            st.rerun()
    df = read_table("food")
    st.dataframe(df, use_container_width=True)
    delete_ui("food", df)

with tabs[6]:
    st.subheader("Log Vitamins / Supplements")
    with st.form("vitamins_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            v_date = st.date_input("Date", value=date.today(), key="v_date")
            v_name = st.text_input("Vitamin / Supplement name (e.g. Vitamin D, Magnesium)")
        with c2:
            v_dose = st.text_input("Dose (e.g. 2000 IU, 400mg)")
            v_taken = st.checkbox("Taken", value=True)
        v_notes = st.text_input("Notes", key="v_notes")
        if st.form_submit_button("Save"):
            insert_row("vitamins", {"log_date": str(v_date), "vitamin_name": v_name,
                                     "dose": v_dose, "taken": v_taken, "notes": v_notes})
            st.success("Saved.")
            st.rerun()
    df = read_table("vitamins")
    st.dataframe(df, use_container_width=True)
    delete_ui("vitamins", df)

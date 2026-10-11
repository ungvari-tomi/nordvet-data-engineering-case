"""Local Streamlit dashboard for the Nordvet no-show view."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from src.dashboard_data import (
    get_available_dates,
    get_clinic_options,
    get_clinic_period_metrics,
    get_dimension_metrics,
    get_period_trends,
    get_rolling_month_clinic_metrics,
)


DATABASE_PATH = Path("data/nordvet.sqlite3")

st.set_page_config(page_title="Nordvet clinic no-shows", layout="wide")
st.markdown(
    """
    <style>
    .stApp {
        background: #F5F7F4;
    }
    .block-container {
        max-width: 1440px;
        padding-top: 2.2rem;
        padding-bottom: 3rem;
    }
    [data-testid="stHeader"] {
        background: rgba(245, 247, 244, 0.92);
    }
    .hero {
        padding: 1.65rem 1.9rem;
        margin: 0 0 1.5rem;
        background: linear-gradient(115deg, #123F3A, #18766D);
        border-radius: 16px;
        color: #FFFFFF;
        box-shadow: 0 8px 24px rgba(25, 59, 53, 0.12);
    }
    .hero-label {
        color: #BFE1D8;
        font-size: 0.76rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        margin-bottom: 0.45rem;
    }
    .hero h1 {
        color: #FFFFFF;
        font-size: 2rem;
        line-height: 1.2;
        margin: 0;
        letter-spacing: -0.035em;
    }
    .hero p {
        color: #E0F0EB;
        font-size: 0.98rem;
        margin: 0.55rem 0 0;
    }
    h2 {
        color: #203F39;
        letter-spacing: -0.025em;
        margin-top: 1.5rem;
    }
    [data-testid="stMetric"] {
        background: #FFFFFF;
        border: 1px solid #E3EAE5;
        border-radius: 12px;
        padding: 1rem 1.1rem;
        box-shadow: 0 3px 12px rgba(32, 51, 47, 0.045);
    }
    [data-testid="stMetricLabel"] {
        color: #657771;
    }
    [data-testid="stMetricValue"] {
        color: #174E47;
    }
    [data-testid="stDataFrame"] {
        border: 1px solid #E3EAE5;
        border-radius: 10px;
        overflow: hidden;
    }
    [data-testid="stCaptionContainer"] {
        color: #64766F;
    }
    hr {
        border-color: #E1E8E3;
    }
    </style>
    <div class="hero">
        <div class="hero-label">Nordvet · Operations</div>
        <h1>Clinic no-show analysis</h1>
        <p>A clear view of missed appointments, clinic patterns, and trends.</p>
    </div>
    """,
    unsafe_allow_html=True,
)
st.caption("Local report from the SQLite extract · This dashboard does not call the VetOne API.")

if not DATABASE_PATH.is_file():
    st.error(f"Database not found: {DATABASE_PATH}. Run the data pipeline first.")
    st.stop()

try:
    available_dates = get_available_dates(DATABASE_PATH)
except Exception as exc:
    st.error(f"Could not read appointment data from {DATABASE_PATH}: {exc}")
    st.stop()

if not available_dates:
    st.info("No non-deleted appointment dates are available in the database yet.")
    st.stop()

yesterday = (date.today() - timedelta(days=1)).isoformat()
default_date = yesterday if yesterday in available_dates else available_dates[-1]
selected_date = st.selectbox(
    "Appointment date",
    options=available_dates,
    index=available_dates.index(default_date),
)
if selected_date != yesterday:
    st.info(
        f"No data for yesterday ({yesterday}) is available. "
        f"Showing the latest selected date with data: {selected_date}."
        if yesterday not in available_dates
        else f"Showing {selected_date}; select yesterday to view yesterday's results."
    )

rolling_month_start = (
    date.fromisoformat(selected_date) - timedelta(days=29)
).isoformat()
try:
    rolling_month_rows = get_rolling_month_clinic_metrics(
        DATABASE_PATH,
        selected_date,
    )
except Exception as exc:
    st.error(f"Could not load monthly clinic summary: {exc}")
    st.stop()

st.subheader("Past 30 days · clinic highlights")
st.caption(f"{rolling_month_start} to {selected_date} · based on the selected date.")
summary_rows = [
    {
        "Clinic": row["clinic_name"] or row["clinic_id"],
        "Clinic ID": row["clinic_id"],
        "No-shows": row["no_shows"],
        "Outcome appointments": row["outcomes"],
        "No-show rate": row["no_show_rate"],
    }
    for row in rolling_month_rows
]
summary = pd.DataFrame(summary_rows)
top_by_count = summary.sort_values(
    ["No-shows", "No-show rate", "Clinic"],
    ascending=[False, False, True],
    na_position="last",
).head(5)
top_by_rate = summary.dropna(subset=["No-show rate"]).sort_values(
    ["No-show rate", "No-shows", "Clinic"],
    ascending=[False, False, True],
).head(5)
summary_columns = st.columns(2)
with summary_columns[0]:
    st.markdown("**5 clinics with the most no-shows**")
    st.dataframe(
        top_by_count,
        hide_index=True,
        width="stretch",
        column_config={
            "No-show rate": st.column_config.NumberColumn(
                "No-show rate",
                format="percent",
            )
        },
    )
with summary_columns[1]:
    st.markdown("**5 clinics with the highest no-show rate**")
    st.dataframe(
        top_by_rate,
        hide_index=True,
        width="stretch",
        column_config={
            "No-show rate": st.column_config.NumberColumn(
                "No-show rate",
                format="percent",
            )
        },
    )
st.caption(
    "No-show rate = no-shows ÷ (completed + no-shows). Cancelled, booked, and "
    "deleted appointments are excluded. Outcome volume is shown for context; "
    "the rate ranking has no minimum-volume threshold."
)

period_columns = st.columns([1, 2])
with period_columns[0]:
    ranking_period = st.selectbox("Clinic ranking period", ["Daily", "Weekly", "Monthly"])
with period_columns[1]:
    st.caption(
        "Daily uses the selected date; weekly uses Monday–Sunday; monthly uses "
        "the selected date's calendar month."
    )

try:
    clinic_rows = get_clinic_period_metrics(
        DATABASE_PATH, selected_date, ranking_period
    )
except Exception as exc:
    st.error(f"Could not load clinic metrics: {exc}")
    st.stop()

total_no_shows = sum(row["no_shows"] for row in clinic_rows)
total_completed = sum(row["completed"] for row in clinic_rows)
total_cancelled = sum(row["cancelled"] for row in clinic_rows)
total_booked = sum(row["booked"] for row in clinic_rows)
total_outcomes = total_no_shows + total_completed
total_rate = total_no_shows / total_outcomes if total_outcomes else 0.0

metric_columns = st.columns(4)
metric_columns[0].metric("No-shows", f"{total_no_shows:,}")
metric_columns[1].metric("No-show rate", f"{total_rate:.1%}")
metric_columns[2].metric("Completed", f"{total_completed:,}")
metric_columns[3].metric("Cancelled", f"{total_cancelled:,}")
st.caption(
    f"Rate denominator: completed + no-shows ({total_outcomes:,} appointments). "
    f"Booked appointments ({total_booked:,}) and cancelled appointments are excluded."
)
st.subheader(f"Clinic rankings · {ranking_period.lower()} period")
chart_metric = st.radio(
    "Comparison chart metric",
    ["No-show rate", "No-show count"],
    horizontal=True,
    key="comparison_chart_metric",
)

display_rows = [
    {
        "Clinic": row["clinic_name"] or row["clinic_id"],
        "Clinic ID": row["clinic_id"],
        "No-shows": row["no_shows"],
        "Completed": row["completed"],
        "Outcome appointments": row["outcomes"],
        "No-show rate": row["no_show_rate"],
        "Cancelled": row["cancelled"],
        "Booked": row["booked"],
    }
    for row in clinic_rows
]
table = pd.DataFrame(display_rows)
table = table.sort_values(
    ["No-show rate", "No-shows", "Clinic"],
    ascending=[False, False, True],
    na_position="last",
)
clinic_chart_table = table.sort_values(
    ["No-show rate", "No-shows", "Clinic"]
    if chart_metric == "No-show rate"
    else ["No-shows", "No-show rate", "Clinic"],
    ascending=[False, False, True],
    na_position="last",
)
top_clinics = table.head(10)
clinic_chart_rows = clinic_chart_table.dropna(subset=["No-show rate"])
clinic_chart_rows = clinic_chart_rows.head(10)

left, right = st.columns([3, 2])
with left:
    if clinic_chart_rows.empty:
        st.info("No completed or no-show appointments are available for this date.")
    else:
        chart_column = (
            "No-show rate" if chart_metric == "No-show rate" else "No-shows"
        )
        chart_axis = (
            alt.Axis(format="%") if chart_metric == "No-show rate" else alt.Axis()
        )
        rate_chart = (
            alt.Chart(clinic_chart_rows)
            .mark_bar(color="#18766D", cornerRadiusEnd=3)
            .encode(
                x=alt.X(
                    f"{chart_column}:Q",
                    axis=chart_axis,
                    title=chart_metric,
                ),
                y=alt.Y(
                    "Clinic:N",
                    sort=alt.SortField(
                        field=chart_column,
                        order="descending",
                    ),
                    title=None,
                ),
                tooltip=[
                    alt.Tooltip("Clinic:N"),
                    alt.Tooltip("No-show rate:Q", format=".1%"),
                    alt.Tooltip("No-shows:Q"),
                    alt.Tooltip("Outcome appointments:Q"),
                ],
            )
            .properties(height=340)
            .configure(background="transparent")
            .configure_axis(
                labelColor="#536760",
                titleColor="#536760",
                gridColor="#E6ECE7",
                domainColor="#D8E2DC",
            )
            .configure_view(strokeOpacity=0)
        )
        st.altair_chart(rate_chart, use_container_width=True)
with right:
    st.caption(
        "Top 10 clinics by no-show rate; the chart uses the selected metric. "
        "The full ranking is shown below."
    )
    st.dataframe(
        top_clinics,
        hide_index=True,
        width="stretch",
        column_config={
            "No-show rate": st.column_config.NumberColumn(
                "No-show rate",
                format="percent",
            )
        },
    )

with st.expander("Show all clinics"):
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "No-show rate": st.column_config.NumberColumn(
                "No-show rate",
                format="percent",
            )
        },
    )

st.subheader("No-show trend")
clinic_options = get_clinic_options(DATABASE_PATH)
clinic_names = {
    clinic["clinic_id"]: clinic["clinic_name"] for clinic in clinic_options
}
selected_trend_clinic = st.selectbox(
    "Trend clinic",
    options=[""] + [clinic["clinic_id"] for clinic in clinic_options],
    format_func=lambda clinic_id: (
        "All clinics" if not clinic_id else clinic_names[clinic_id]
    ),
)
trend_period = st.radio(
    "Trend interval",
    ["Weekly", "Monthly"],
    horizontal=True,
)
try:
    trend_rows = get_period_trends(
        DATABASE_PATH,
        trend_period,
        selected_trend_clinic or None,
    )
except Exception as exc:
    st.error(f"Could not load no-show trend: {exc}")
    st.stop()

if trend_rows:
    trend = pd.DataFrame(trend_rows).set_index("period_start")
    trend_columns = st.columns(2)
    with trend_columns[0]:
        st.caption(f"No-show count by {trend_period.lower()} period")
        st.line_chart(trend[["no_shows"]])
    with trend_columns[1]:
        st.caption(f"No-show rate by {trend_period.lower()} period")
        st.line_chart(trend[["no_show_rate"]])
else:
    st.info("No completed or no-show appointments are available for trend analysis.")

st.caption(
    "Trend periods are Monday-starting weeks or calendar months. The first and "
    "last periods may be partial depending on the data loaded."
)

st.subheader(f"No-show patterns · {ranking_period.lower()} period")
st.caption(
    f"Charts are sorted by {chart_metric.lower()}. Rates use completed + "
    "no-show appointments. Cancelled, booked, and deleted appointments are "
    "excluded. Vet comparisons are descriptive, not a standalone "
    "staff-performance measure."
)
try:
    service_rows = get_dimension_metrics(
        DATABASE_PATH,
        selected_date,
        ranking_period,
        "service",
    )
    vet_rows = get_dimension_metrics(
        DATABASE_PATH,
        selected_date,
        ranking_period,
        "vet",
    )
except Exception as exc:
    st.error(f"Could not load service or vet comparisons: {exc}")
    st.stop()

service_table = pd.DataFrame(
    [
        {
            "Service": row["category"],
            "No-shows": row["no_shows"],
            "Completed": row["completed"],
            "Outcome appointments": row["outcomes"],
            "No-show rate": row["no_show_rate"],
        }
        for row in service_rows
    ]
)
sort_fields = (
    ["No-show rate", "No-shows", "Service"]
    if chart_metric == "No-show rate"
    else ["No-shows", "No-show rate", "Service"]
)
service_table = service_table.sort_values(
    sort_fields,
    ascending=[False, False, True],
    na_position="last",
)
vet_table = pd.DataFrame(
    [
        {
            "Vet ID": row["category"],
            "No-shows": row["no_shows"],
            "Completed": row["completed"],
            "Outcome appointments": row["outcomes"],
            "No-show rate": row["no_show_rate"],
        }
        for row in vet_rows
    ]
)
vet_sort_fields = (
    ["No-show rate", "No-shows", "Vet ID"]
    if chart_metric == "No-show rate"
    else ["No-shows", "No-show rate", "Vet ID"]
)
vet_table = vet_table.sort_values(
    vet_sort_fields,
    ascending=[False, False, True],
    na_position="last",
)

chart_column = "No-show rate" if chart_metric == "No-show rate" else "No-shows"
chart_axis = alt.Axis(format="%") if chart_metric == "No-show rate" else alt.Axis()
chart_columns = st.columns(2)
with chart_columns[0]:
    st.markdown("**By service**")
    chart_data = service_table.dropna(subset=["No-show rate"])
    if chart_data.empty:
        st.info("No completed or no-show appointments in this period.")
    else:
        service_chart = (
            alt.Chart(chart_data)
            .mark_bar(color="#18766D", cornerRadiusEnd=3)
            .encode(
                x=alt.X(chart_column + ":Q", axis=chart_axis, title=chart_metric),
                y=alt.Y(
                    "Service:N",
                    sort=alt.SortField(field=chart_column, order="descending"),
                    title=None,
                ),
                tooltip=[
                    alt.Tooltip("Service:N"),
                    alt.Tooltip("No-show rate:Q", format=".1%"),
                    alt.Tooltip("No-shows:Q"),
                    alt.Tooltip("Outcome appointments:Q"),
                ],
            )
            .properties(height=300)
            .configure(background="transparent")
            .configure_axis(
                labelColor="#536760",
                titleColor="#536760",
                gridColor="#E6ECE7",
                domainColor="#D8E2DC",
            )
            .configure_view(strokeOpacity=0)
        )
        st.altair_chart(service_chart, use_container_width=True)
        st.dataframe(
            service_table,
            hide_index=True,
            width="stretch",
            column_config={
                "No-show rate": st.column_config.NumberColumn(
                    "No-show rate",
                    format="percent",
                )
            },
        )
with chart_columns[1]:
    st.markdown(f"**By vet · top 10 by {chart_metric.lower()}**")
    top_vets = vet_table.dropna(subset=["No-show rate"]).head(10)
    if top_vets.empty:
        st.info("No completed or no-show appointments in this period.")
    else:
        vet_chart = (
            alt.Chart(top_vets)
            .mark_bar(color="#18766D", cornerRadiusEnd=3)
            .encode(
                x=alt.X(chart_column + ":Q", axis=chart_axis, title=chart_metric),
                y=alt.Y(
                    "Vet ID:N",
                    sort=alt.SortField(field=chart_column, order="descending"),
                    title=None,
                ),
                tooltip=[
                    alt.Tooltip("Vet ID:N"),
                    alt.Tooltip("No-show rate:Q", format=".1%"),
                    alt.Tooltip("No-shows:Q"),
                    alt.Tooltip("Outcome appointments:Q"),
                ],
            )
            .properties(height=340)
            .configure(background="transparent")
            .configure_axis(
                labelColor="#536760",
                titleColor="#536760",
                gridColor="#E6ECE7",
                domainColor="#D8E2DC",
            )
            .configure_view(strokeOpacity=0)
        )
        st.altair_chart(vet_chart, use_container_width=True)
        st.dataframe(
            top_vets,
            hide_index=True,
            width="stretch",
            column_config={
                "No-show rate": st.column_config.NumberColumn(
                    "No-show rate",
                    format="percent",
                )
            },
        )
        with st.expander("Show all vets"):
            st.dataframe(
                vet_table,
                hide_index=True,
                width="stretch",
                column_config={
                    "No-show rate": st.column_config.NumberColumn(
                        "No-show rate",
                        format="percent",
                    )
                },
            )

st.caption(
    "Clinics without outcome appointments have no rate (shown as blank). "
    "Deleted appointments are excluded. Confirm status definitions with the API owner."
)

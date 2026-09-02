import os
import re
import threading
import time
import traceback
import uuid
import pandas as pd
from flask import Flask, jsonify, redirect, render_template, request, url_for

from config import Config
from process import (
    TASKS_STATUS,
    DiscogsError,
    initiate_task,
    normalize_seller_input,
    parse_and_validate_sell_list_url,
    save_uuid_to_file,
)

app = Flask(__name__)
app.config.from_object(Config)
_in_flight_tasks = {}
_in_flight_lock = threading.Lock()


def task_key(form_data):
    if form_data.get("mode") == "url":
        return ("url", form_data["marketplace_url"])
    return ("seller", form_data["user_input"].casefold())


def run_task_and_release(form_data, app_instance, unique_id, key):
    try:
        initiate_task(form_data, app_instance, unique_id)
    finally:
        with _in_flight_lock:
            if _in_flight_tasks.get(key) == unique_id:
                _in_flight_tasks.pop(key, None)


def price_to_number(value):
    text = re.sub(r"[^0-9,.-]", "", str(value or ""))
    if not text:
        return 0.0
    if "." in text and "," in text:
        decimal = "." if text.rfind(".") > text.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        text = text.replace(thousands, "").replace(decimal, ".")
    elif "," in text:
        parts = text.split(",")
        text = (
            "".join(parts[:-1]) + "." + parts[-1]
            if len(parts[-1]) == 2
            else "".join(parts)
        )
    try:
        return float(text)
    except ValueError:
        return 0.0


@app.route("/", methods=["POST", "GET"])
def index():
    if request.method == "POST":
        seller_input = request.form.get("user_input", "").strip()
        filtered_url = request.form.get("filtered_url", "").strip()

        form_data = {
            "mode": "seller",
            "user_input": seller_input,
            "filtered_url": filtered_url,
            "vinyls": "",  # Initialize empty
            "genre": "",
            "style": "",
        }

        if not seller_input and not filtered_url:
            return jsonify(
                success=False,
                message=(
                    "Please provide a seller name or a Discogs /shop/list/, "
                    "legacy /sell/list, or seller profile URL"
                ),
            )

        if filtered_url:
            try:
                url_data = parse_and_validate_sell_list_url(filtered_url)
            except DiscogsError as exc:
                return jsonify(success=False, message=str(exc))

            form_data["mode"] = "url"
            form_data["marketplace_url"] = url_data["marketplace_url"]
        else:
            try:
                form_data["user_input"] = normalize_seller_input(seller_input)
            except DiscogsError as exc:
                return jsonify(success=False, message=str(exc))

        key = task_key(form_data)
        with _in_flight_lock:
            unique_id = _in_flight_tasks.get(key)
            current_status = TASKS_STATUS.get(unique_id) if unique_id else None
            if not current_status or current_status.get("completed"):
                unique_id = str(uuid.uuid4())
                TASKS_STATUS[unique_id] = {
                    "completed": False,
                    "error": None,
                    "processed": 0,
                    "total": None,
                    "phase": "queued",
                    "started_at": time.time(),
                    "updated_at": time.time(),
                }
                _in_flight_tasks[key] = unique_id
                threading.Thread(
                    target=run_task_and_release,
                    args=(form_data, app, unique_id, key),
                    daemon=True,
                ).start()
        return jsonify(
            success=True,
            message="",
            unique_id=unique_id,
        )
    return render_template("index.html")


@app.route("/task_status/<unique_id>")
def task_status(unique_id):
    if unique_id not in TASKS_STATUS:
        try:
            valid_uuid = str(uuid.UUID(unique_id)) == unique_id
        except ValueError:
            valid_uuid = False
        if valid_uuid and os.path.exists(f"data/pages/{unique_id}.csv"):
            return jsonify(
                completed=True,
                error=None,
                processed=None,
                total=None,
                phase="complete",
                elapsed=None,
                idle_for=0,
            )
        return jsonify(error="Invalid task id", completed=None), 404
    task = TASKS_STATUS[unique_id]
    started_at = task.get("started_at")
    updated_at = task.get("updated_at") or started_at
    return jsonify(
        completed=task["completed"],
        error=task.get("error"),
        processed=task.get("processed", 0),
        total=task.get("total"),
        phase=task.get("phase", "queued"),
        elapsed=round(max(0, time.time() - started_at)) if started_at else None,
        idle_for=round(max(0, time.time() - updated_at)) if updated_at else None,
    )


@app.route("/table/")
def render_table():
    unique_id = str(uuid.uuid4())
    save_uuid_to_file(unique_id)
    return redirect(url_for("render_table_with_id", unique_id=unique_id))


@app.route("/table/<unique_id>")
def render_table_with_id(unique_id):
    file_path = f"data/pages/{unique_id}.csv"
    if not os.path.exists(file_path):
        return "Seller's collection with this ID does not exist", 404
    return render_template("table.html", unique_id=unique_id)


@app.route("/table_data/<unique_id>", methods=["POST"])
def serve_table_data(unique_id):
    try:
        print(f"\n=== Processing request for table_data/{unique_id} ===")
        print(f"Request form data: {request.form}")

        df = pd.read_csv(f"data/pages/{unique_id}.csv")
        print(f"Loaded CSV with {len(df)} total records")

        total_records = len(df)
        draw = int(request.form.get("draw", 0))
        start = int(request.form.get("start", 0))
        length = int(request.form.get("length", 250))
        search_value = request.form.get("search[value]", "")
        order_column = int(request.form.get("order[0][column]", 0))
        order_direction = request.form.get("order[0][dir]", "asc")

        print(f"\nPagination params:")
        print(f"- Start: {start}")
        print(f"- Length: {length}")
        print(f"- Search value: {search_value}")
        print(f"- Order column: {order_column}")
        print(f"- Order direction: {order_direction}")

        if search_value:
            search_value = search_value.replace("\\", "\\\\")
            search_value = re.escape(search_value)
            query = "|".join(
                [
                    f'{col}.str.contains("{search_value}", case=False, na=False)'
                    for col in df.columns
                    if df[col].dtype == "object"
                ]
            )
            if query:
                df = df[df.eval(query)]
                print(f"\nAfter search filter: {len(df)} records remaining")

        if order_column < len(df.columns):
            print(f"\nSorting by column: {df.columns[order_column]}")
            col_name = df.columns[order_column]
            sorted_df = df.copy()
            if col_name == "price":
                sorted_df["sort_val"] = sorted_df[col_name].map(price_to_number)
                print("Applied price sorting conversion")

            elif sorted_df[col_name].dtype in ["int64", "float64"]:
                sorted_df["sort_val"] = sorted_df[col_name]
            else:
                sorted_df["sort_val"] = sorted_df[col_name].astype(str)

            # Perform sorting
            sorted_df.sort_values(
                by="sort_val", ascending=(order_direction == "asc"), inplace=True
            )
            sorted_df.drop(columns=["sort_val"], inplace=True)  # drop auxiliary column
        else:
            sorted_df = df.copy()

        # Extract the necessary subset after sorting
        df_subset = sorted_df.iloc[start : start + length]
        print(f"\nReturning subset of {len(df_subset)} records")

        data = []
        for _, row in df_subset.iterrows():
            data.append(row.tolist())

        response_data = {
            "draw": draw,
            "recordsTotal": total_records,
            "recordsFiltered": len(df),
            "data": data,
        }

        print("\nResponse summary:")
        print(f"- Total records: {response_data['recordsTotal']}")
        print(f"- Filtered records: {response_data['recordsFiltered']}")
        print(f"- Records in this chunk: {len(response_data['data'])}")
        print("=== End of request processing ===\n")

        return jsonify(response_data)

    except Exception as e:
        print("\n=== ERROR in table_data processing ===")
        print("Error Occurred: ", str(e))
        print(traceback.format_exc())
        print("=== End of error trace ===\n")
        return "Internal Server Error", 500


if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "5080"))
    app.run(debug=True, host=host, port=port)

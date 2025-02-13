import os
import re
import threading
import traceback
import uuid
from urllib.parse import quote_plus
import pandas as pd
from flask import Flask, jsonify, redirect, render_template, request, url_for

from config import Config
from process import TASKS_STATUS, initiate_task, save_uuid_to_file, verify_seller

app = Flask(__name__)
app.config.from_object(Config)


@app.route("/", methods=["POST", "GET"])
def index():
    if request.method == "POST":
        unique_id = str(uuid.uuid4())
        TASKS_STATUS[unique_id] = {"completed": False}

        # Get the genre and style values
        genre = request.form.get("genre", "").strip()
        style = request.form.get("style", "").strip()

        form_data = {
            "user_input": request.form.get("user_input"),
            "vinyls": "",  # Initialize empty
            "genre": "",  # Initialize empty
            "style": "",  # Initialize empty
        }

        # Only add parameters if they are provided
        if request.form.get("vinyls_only") == "on":
            form_data["vinyls"] = "&format=Vinyl"
        if genre:
            form_data["genre"] = f"&genre={quote_plus(genre)}"
        if style:
            form_data["style"] = f"&style={quote_plus(style)}"

        print(f"Form data: {form_data}")  # Debug print
        is_seller = verify_seller(form_data["user_input"])

        if not is_seller:
            return jsonify(
                success=False,
                message="This seller does not exist or does not offer any records for sale",
            )
        else:
            threading.Thread(
                target=initiate_task, args=(form_data, app, unique_id)
            ).start()
            return jsonify(
                success=True,
                message="Getting data... (May take up to a minute)",
                unique_id=unique_id,
            )
    return render_template("index.html")


@app.route("/task_status/<unique_id>")
def task_status(unique_id):
    if unique_id not in TASKS_STATUS:
        return jsonify(error="Invalid task id", completed=None), 404
    return jsonify(completed=TASKS_STATUS[unique_id]["completed"])


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
                sorted_df["sort_val"] = (
                    sorted_df[col_name]
                    .replace(r"[€$£A$CA$CHF¥SEKR$MX$NZ$DKKZAR,]", "", regex=True)
                    .astype(float)
                )
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
    app.run(debug=True)

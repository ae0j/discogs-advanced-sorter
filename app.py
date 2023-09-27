from flask import (
    Flask,
    redirect,
    render_template,
    request,
    jsonify,
    url_for,
)
from process import (
    verify_seller,
    save_uuid_to_file,
    is_valid_uuid,
    initiate_task,
    TASK_STATUS,
)
from config import Config

import threading
import uuid
import pandas as pd
import traceback
import re

app = Flask(__name__)
app.config.from_object(Config)


@app.route("/", methods=["POST", "GET"])
def index():
    if request.method == "POST":
        user_input = request.form.get("user_input")
        seller = verify_seller(user_input)
        print(f"seller: {seller}")

        if not seller:
            return jsonify(
                success=False,
                message="This seller does not exist or does not offer any records for sale",
            )
        else:
            threading.Thread(target=initiate_task, args=(user_input, app)).start()
            return jsonify(
                success=True, message="Getting data... (May take up to a minute)"
            )

    return render_template("index.html")


@app.route("/task_status")
def task_status():
    return jsonify(completed=TASK_STATUS["completed"])


@app.route("/table/")
def render_table():
    unique_id = str(uuid.uuid4())
    save_uuid_to_file(unique_id)
    return redirect(url_for("render_table_with_id", unique_id=unique_id))


@app.route("/table/<unique_id>")
def render_table_with_id(unique_id):
    if not is_valid_uuid(unique_id):
        return "Invalid URL", 404
    return render_template("table.html", unique_id=unique_id)


@app.route("/table_data/<unique_id>", methods=["POST"])
def serve_table_data(unique_id):
    try:
        print(f"Received unique_id: {unique_id}")
        if not is_valid_uuid(unique_id):
            return "Invalid URL", 404

        df = pd.read_csv("data/pages/result.csv")

        total_records = len(df)
        draw = int(request.form.get("draw", 0))
        start = int(request.form.get("start", 0))
        length = int(request.form.get("length", 250))
        search_value = request.form.get("search[value]", "")
        order_column = int(request.form.get("order[0][column]", 0))
        order_direction = request.form.get("order[0][dir]", "asc")

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

        if order_column < len(df.columns):
            col_name = df.columns[order_column]
            sorted_df = df.copy()
            if col_name == "price":
                sorted_df["sort_val"] = (
                    sorted_df[col_name]
                    .replace(r"[€$£A$CA$CHF¥SEKR$MX$NZ$DKKZAR,]", "", regex=True)
                    .astype(float)
                )

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

        data = []
        for _, row in df_subset.iterrows():
            data.append(row.tolist())

        response_data = {
            "draw": draw,
            "recordsTotal": total_records,
            "recordsFiltered": len(df),
            "data": data,
        }

        return jsonify(response_data)

    except Exception as e:
        print("Error Occurred: ", str(e))
        print(traceback.format_exc())
        return "Internal Server Error", 500


@app.route("/generate_table")
def generate_table_request():
    unique_id = str(uuid.uuid4())
    save_uuid_to_file(unique_id)  # Save the UUID to file
    return redirect(url_for("serve_table_data"))


if __name__ == "__main__":
    app.run(debug=True)

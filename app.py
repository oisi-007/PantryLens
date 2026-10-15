from datetime import date
import html
import re

import pandas as pd
import requests
import streamlit as st

import os
import threading
import uvicorn

if hasattr(st, "secrets"):
    for key, value in st.secrets.items():
        os.environ[key] = str(value)

@st.cache_resource
def start_background_backend():
    try:
        from main import app as fastapi_app
        thread = threading.Thread(
            target=lambda: uvicorn.run(fastapi_app, host="127.0.0.1", port=8000, log_level="warning"),
            daemon=True
        )
        thread.start()
    except Exception as e:
        print(f"Backend background start error: {e}")

start_background_backend()

DEFAULT_API_URL = "http://127.0.0.1:8000"
DIETARY_PROFILES = [
    "Vegetarian",
    "Jain",
    "Vegan",
    "Eggetarian",
    "Non-Vegetarian",
]
ALLERGENS = ["dairy", "gluten", "nuts", "sesame", "soy", "shellfish"]
EXCLUDED_INGREDIENTS = ["onion", "garlic"]


def apply_theme(mode):
    if mode == "Dark":
        background = "#121212"
        surface = "#1E1E1E"
        foreground = "#F5F5F5"
        accent = "#1B5E20"
        highlight = "#FFD54F"
        border = "#383838"
    else:
        background = "#FFFFFF"
        surface = "#F7F9F7"
        foreground = "#202520"
        accent = "#2E7D32"
        highlight = "#FFC107"
        border = "#DDE5DD"

    dark_data_styles = ""
    if mode == "Dark":
        dark_data_styles = f"""
        [data-testid="stDataFrame"],
        [data-testid="stTable"],
        [data-testid="stMetric"] {{
            background-color: {surface} !important;
            color: {foreground} !important;
            border-color: {border} !important;
        }}
        [data-testid="stDataFrame"] > div,
        .stDataFrame,
        .stTable,
        div[data-testid="stTable"],
        table,
        th,
        td,
        [data-testid="stTable"] table,
        [data-testid="stTable"] thead,
        [data-testid="stTable"] tbody,
        [data-testid="stTable"] tr,
        [data-testid="stTable"] th,
        [data-testid="stTable"] td,
        div[data-testid="stFileUploader"],
        section[data-testid="stFileUploaderDropzone"] {{
            background-color: {surface} !important;
            color: {foreground} !important;
            border-color: {border} !important;
        }}
        div[data-testid="stFileUploader"] p,
        div[data-testid="stFileUploader"] label,
        div[data-testid="stFileUploader"] span,
        div[data-testid="stFileUploader"] small,
        section[data-testid="stFileUploaderDropzone"] p,
        section[data-testid="stFileUploaderDropzone"] label,
        section[data-testid="stFileUploaderDropzone"] span,
        section[data-testid="stFileUploaderDropzone"] small {{
            color: {foreground} !important;
        }}
        [data-testid="stMetric"] [data-testid="stMetricLabel"],
        [data-testid="stMetric"] [data-testid="stMetricValue"],
        [data-testid="stMetric"] [data-testid="stMetricDelta"] {{
            color: {foreground} !important;
        }}
        """

    st.markdown(
        f"""
        <style>
        :root {{
            color-scheme: {"dark" if mode == "Dark" else "light"};
        }}
        .stApp, [data-testid="stAppViewContainer"] {{
            background-color: {background};
            color: {foreground};
        }}
        [data-testid="stHeader"], [data-testid="stSidebar"] {{
            background-color: {background};
            color: {foreground};
        }}
        [data-testid="stSidebar"] {{
            border-right: 1px solid {border};
        }}
        [data-testid="stMetric"] {{
            background: {surface};
            border: 1px solid {border};
            border-left: 4px solid {accent};
            border-radius: 0.7rem;
            padding: 0.8rem 1rem;
        }}
        h1, h2, h3, p, label, [data-testid="stMarkdownContainer"] {{
            color: {foreground};
        }}
        [data-testid="stCaptionContainer"] {{
            color: {foreground};
            opacity: 0.78;
        }}
        .stButton > button, .stFormSubmitButton > button {{
            background-color: {accent};
            border: 1px solid {accent};
            color: #FFFFFF;
            border-radius: 0.55rem;
            font-weight: 600;
        }}
        .stButton > button:hover, .stFormSubmitButton > button:hover {{
            background-color: {highlight};
            border-color: {highlight};
            color: #121212;
        }}
        [data-testid="stTabs"] button[aria-selected="true"] {{
            color: {accent};
            border-bottom-color: {highlight};
        }}
        [data-testid="stDataFrame"], [data-testid="stTable"] {{
            border: 1px solid {border};
            border-radius: 0.6rem;
        }}
        {dark_data_styles}
        div[data-baseweb="input"] > div,
        div[data-baseweb="select"] > div,
        div[data-baseweb="textarea"] > div {{
            background-color: {surface};
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def api_request(method, base_url, path, **kwargs):
    try:
        response = requests.request(
            method,
            f"{base_url.rstrip('/')}{path}",
            timeout=8,
            **kwargs,
        )
        response.raise_for_status()
    except requests.ConnectionError:
        st.error(
            f"Could not connect to the PantryLens API at {base_url}. "
            "Start the backend with `uvicorn main:app --reload`."
        )
        return None
    except requests.Timeout:
        st.error("The PantryLens API did not respond in time. Please try again.")
        return None
    except requests.HTTPError as error:
        detail = error.response.text if error.response is not None else str(error)
        st.error(f"PantryLens API returned an error: {detail}")
        return None
    except requests.RequestException as error:
        st.error(f"Could not complete the API request: {error}")
        return None

    try:
        return response.json()
    except requests.exceptions.JSONDecodeError:
        st.error("The PantryLens API returned an invalid JSON response.")
        return None


def _get_item_list(payload, endpoint_name):
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        st.error(f"The {endpoint_name} response did not contain an items list.")
        return None
    return payload["items"]


def _style_inventory(dataframe):
    def color_status(row):
        freshness_styles = {
            "expired": "background-color: #FDE2E2; color: #8B1E1E",
            "expiring_soon": "background-color: #FFF3CD; color: #795600",
            "fresh": "background-color: #E2F2E5; color: #205B2B",
        }
        stock_styles = {
            "out_of_stock": "font-weight: bold; color: #B42318",
            "low_stock": "font-weight: bold; color: #A15C00",
        }
        row_styles = [""] * len(row)
        columns = list(row.index)
        if "freshness_status" in columns:
            row_styles[columns.index("freshness_status")] = freshness_styles.get(
                row["freshness_status"], ""
            )
        if "stock_status" in columns:
            row_styles[columns.index("stock_status")] = stock_styles.get(
                row["stock_status"], ""
            )
        return row_styles

    return _format_dataframe(dataframe).apply(color_status, axis=1)


def _format_dataframe(dataframe):
    """Format floating-point columns consistently without changing their values."""
    dataframe = dataframe.apply(
        lambda column: column.map(_replace_html_line_breaks)
    )
    float_columns = dataframe.select_dtypes(include=["floating"]).columns
    formatters = {column: "{:.1f}" for column in float_columns}
    return dataframe.style.format(formatters)


def _replace_html_line_breaks(value):
    if isinstance(value, str):
        return re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    return value


def _initialize_profile_state():
    st.session_state.setdefault("dietary_profile", "Vegetarian")
    st.session_state.setdefault("allergies", [])
    st.session_state.setdefault("excluded_ingredients", [])


def main():
    st.set_page_config(
        page_title="PantryLens | Indian Meal Manager",
        page_icon="PantryLens.Favicon.png",
        layout="wide",
    )
    st.markdown(
        """
        <style>
        table, thead, tbody, th, td,
        [data-testid="stFileUploader"] p,
        [data-testid="stFileUploader"] label,
        [data-testid="stFileUploader"] span,
        [data-testid="stFileUploader"] small {
            color: inherit !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    _initialize_profile_state()
    api_url = DEFAULT_API_URL

    with st.sidebar:
        st.image("PantryLens.Favicon.png", width=120)
        st.title("PantryLens")
        st.caption("Your Indian kitchen, thoughtfully managed.")
        theme_mode = st.radio("Appearance", ["Light", "Dark"], horizontal=True)

    apply_theme(theme_mode)
    st.title("PantryLens")
    st.caption("Reduce waste, plan your next meal, and keep your pantry in check.")

    inventory_tab, shopping_tab, profile_tab, recipe_tab = st.tabs(
        [
            "🥗 Inventory",
            "🛒 Smart Shopping List",
            "🛡️ Dietary Profile & Guardrails",
            "🍛 Recipe Generator",
        ]
    )

    with inventory_tab:
        st.subheader("Your pantry")
        inventory_payload = api_request("GET", api_url, "/inventory")
        inventory_items = (
            _get_item_list(inventory_payload, "inventory")
            if inventory_payload is not None
            else None
        )

        with st.expander("Manually Add Item"):
            with st.form("add_inventory_item_form"):
                item_name = st.text_input("Item Name")
                item_quantity = st.number_input(
                    "Quantity",
                    min_value=0.01,
                    value=1.0,
                    step=0.1,
                )
                item_category = st.selectbox(
                    "Category",
                    [
                        "Produce",
                        "Dairy",
                        "Vegetables",
                        "Grains",
                        "Pantry Staples",
                        "Bakery",
                        "Spices & Seasonings",
                        "Custom...",
                    ],
                )
                custom_category = ""
                if item_category == "Custom...":
                    custom_category = st.text_input("Custom Category")
                item_expiry_date = st.date_input(
                    "Expiry Date",
                    value=date.today(),
                )
                add_item_submitted = st.form_submit_button("Add to Pantry")

            if add_item_submitted:
                if not item_name.strip():
                    st.error("Enter an item name before adding it to the pantry.")
                elif item_category == "Custom..." and not custom_category.strip():
                    st.error("Enter a custom category before adding the item.")
                else:
                    category = (
                        custom_category.strip()
                        if item_category == "Custom..."
                        else item_category
                    )
                    add_item_result = api_request(
                        "POST",
                        api_url,
                        "/inventory",
                        json={
                            "name": item_name.strip(),
                            "quantity": item_quantity,
                            "category": category,
                            "expiry_date": item_expiry_date.isoformat(),
                        },
                    )
                    if add_item_result is not None:
                        st.success("Item added to your pantry.")
                        st.rerun()

        with st.expander("📸 Smart Image & Receipt Ingestion"):
            upload_mode = st.radio(
                "Select Ingestion Source",
                [
                    "Supermarket Bill / Receipt",
                    "Farmer's Market Produce Photos",
                    "Online Delivery Screenshot (Blinkit/Instamart)",
                ],
                horizontal=True,
            )

            uploaded_files = st.file_uploader(
                "Upload image(s)",
                type=["jpg", "jpeg", "png"],
                accept_multiple_files=True,
            )

            if uploaded_files and st.button("Extract & Add to Pantry"):
                # Convert images to base64 or send multipart/form-data to FastAPI
                files_payload = [
                    ("files", (file.name, file.getvalue(), file.type))
                    for file in uploaded_files
                ]
                response = requests.post(
                    f"{api_url}/parse-inventory-images",
                    data={"source_type": upload_mode},
                    files=files_payload,
                )
                if response and response.status_code == 201:
                    st.success(
                        "Successfully extracted items and updated your pantry inventory!"
                    )
                    st.rerun()

        if inventory_items is not None:
            if inventory_items:
                inventory_frame = pd.DataFrame(inventory_items)
                metrics = st.columns(3)
                metrics[0].metric("Pantry items", len(inventory_items))
                metrics[1].metric(
                    "Expiring soon",
                    sum(
                        item.get("freshness_status") == "expiring_soon"
                        for item in inventory_items
                    ),
                )
                metrics[2].metric(
                    "Low or out of stock",
                    sum(
                        item.get("stock_status") in {"low_stock", "out_of_stock"}
                        for item in inventory_items
                    ),
                )
                st.dataframe(
                    _style_inventory(inventory_frame),
                    width="stretch",
                    hide_index=True,
                )
                st.caption(
                    "Expiry cells: red means expired; yellow means expiring within "
                    "3 days; green means fresh. Stock alerts are shown separately."
                )

                consumable_items = [
                    item
                    for item in inventory_items
                    if item.get("quantity", 0) > 0
                ]
                with st.form("consume_form"):
                    st.markdown("#### Log consumption")
                    if not consumable_items:
                        st.info("There are no items with stock available to consume.")
                    else:
                        selected_item = st.selectbox(
                            "Pantry item",
                            consumable_items,
                            format_func=lambda item: (
                                f"{item['name']} — {item['quantity']} available"
                            ),
                        )
                        amount = st.number_input(
                            "Amount consumed",
                            min_value=0.01,
                            max_value=float(selected_item["quantity"]),
                            value=min(1.0, float(selected_item["quantity"])),
                            step=0.1,
                        )
                        logged_date = st.date_input(
                            "Consumption date",
                            value=date.today(),
                            max_value=date.today(),
                        )
                        submitted = st.form_submit_button(
                            "Save consumption",
                            disabled=not consumable_items,
                        )
                        if submitted:
                            result = api_request(
                                "POST",
                                api_url,
                                "/consume",
                                json={
                                    "item_id": selected_item["id"],
                                    "consumed_amount": amount,
                                    "logged_date": logged_date.isoformat(),
                                },
                            )
                            if result is not None:
                                st.success(
                                    f"Consumption logged. "
                                    f"{result['remaining_quantity']} remains."
                                )
                                st.rerun()
            else:
                st.info("Your pantry is empty. Add inventory to the local database to get started.")

    with shopping_tab:
        st.subheader("Predicted restocks")
        shopping_payload = api_request("GET", api_url, "/shopping-list")
        shopping_items = (
            _get_item_list(shopping_payload, "shopping-list")
            if shopping_payload is not None
            else None
        )
        if shopping_items is not None:
            if shopping_items:
                st.dataframe(
                    _format_dataframe(pd.DataFrame(shopping_items)),
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.success(
                    "Nothing needs restocking right now. Your pantry forecast looks good."
                )

    with profile_tab:
        st.subheader("Your dietary preferences")
        st.write(
            "These guardrails are applied before pantry ingredients are sent "
            "to the recipe prompt."
        )
        with st.form("dietary_profile_form"):
            dietary_profile = st.selectbox(
                "Indian dietary profile",
                DIETARY_PROFILES,
                index=DIETARY_PROFILES.index(st.session_state["dietary_profile"]),
            )
            allergies = st.multiselect(
                "Allergies and allergens to avoid",
                ALLERGENS,
                default=st.session_state["allergies"],
                help="Includes common Indian ingredients such as dairy, wheat, and til (sesame).",
            )
            excluded_ingredients = st.multiselect(
                "Additional ingredients to exclude",
                EXCLUDED_INGREDIENTS,
                default=st.session_state["excluded_ingredients"],
                help="Jain profiles exclude onion and garlic automatically, whether or not selected here.",
            )
            save_profile = st.form_submit_button("Save dietary profile")

        if save_profile:
            st.session_state["dietary_profile"] = dietary_profile
            st.session_state["allergies"] = allergies
            st.session_state["excluded_ingredients"] = excluded_ingredients
            st.success("Dietary profile saved for this session.")

        st.info(
            f"Active profile: **{st.session_state['dietary_profile']}** · "
            f"Allergies: **{', '.join(st.session_state['allergies']) or 'None'}** · "
            f"Extra exclusions: **{', '.join(st.session_state['excluded_ingredients']) or 'None'}**"
        )
        if st.session_state["dietary_profile"] == "Jain":
            st.warning("Jain guardrails automatically exclude onion and garlic.")

    with recipe_tab:
        st.subheader("What can I cook?")
        st.write(
            "Get recipe ideas from your pantry. Ingredients nearing expiry are "
            "prioritized, and recipes can combine them with other available items."
        )
        near_expiry_days = st.slider(
            "Include ingredients expiring within (days)",
            min_value=0,
            max_value=14,
            value=3,
        )
        if st.button("🍛 What Can I Cook?", type="primary"):
            recipe_payload = api_request(
                "POST",
                api_url,
                "/generate-recipe",
                json={
                    "user_profile": {
                        "dietary_restrictions": [
                            st.session_state["dietary_profile"].lower()
                        ],
                        "allergies": st.session_state["allergies"],
                        "excluded_ingredients": st.session_state[
                            "excluded_ingredients"
                        ],
                    },
                    "near_expiry_days": near_expiry_days,
                },
            )
            if recipe_payload is not None:
                eligible_items = recipe_payload.get("eligible_items")
                available_items = recipe_payload.get("available_items")
                if (
                    not isinstance(eligible_items, list)
                    or not isinstance(available_items, list)
                ):
                    st.error("The recipe endpoint returned an incomplete response.")
                else:
                    if available_items:
                        st.markdown("#### Available pantry ingredients")
                        st.dataframe(
                            _format_dataframe(pd.DataFrame(available_items)),
                            width="stretch",
                            hide_index=True,
                        )
                    else:
                        st.warning(
                            "There are no available ingredients matching this "
                            "dietary profile."
                        )
                    if not eligible_items:
                        st.info(
                            "No safe ingredients are nearing expiry in this time "
                            "window. Recipes can still use other available pantry items."
                        )
                    # st.markdown("#### Authentic Indian recipe prompt")
                    # st.code(prompt, language="text")

                    if recipe_payload.get("recipe_response"):
                        st.markdown("#### Recipe")
                        recipe_response = _replace_html_line_breaks(
                            recipe_payload["recipe_response"]
                        )
                        recipe_markdown = html.escape(recipe_response).replace(
                            "\n", "  \n"
                        )
                        st.markdown(recipe_markdown)
                    elif recipe_payload.get("message"):
                        st.info(recipe_payload["message"])


if __name__ == "__main__":
    main()

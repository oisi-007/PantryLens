import logging
import math
import os
import re
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import date, datetime

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status, File, Form, UploadFile
from openai import NotFoundError, OpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, field_validator

load_dotenv()

from datetime import date, datetime, timedelta
import database
from guardrails import (
    UserProfile,
    filter_near_expiry_items,
    filter_safe_items,
    format_recipe_prompt,
)
from velocity import analyze_inventory, generate_shopping_list
import base64
import json
from google import genai
from google.genai import types

from google import genai

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_client = (
    genai.Client(api_key=GEMINI_API_KEY)
    if GEMINI_API_KEY
    else None
)

logger = logging.getLogger(__name__)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
groq_client = (
    OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    if GROQ_API_KEY
    else None
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database.initialize_database(database.DEFAULT_DATABASE_PATH)
    yield


app = FastAPI(
    title="PantryLens API",
    description="Pantry inventory, restocking forecasts, and dietary-safe recipe prompts.",
    lifespan=lifespan,
)


class ConsumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: int = Field(gt=0)
    consumed_amount: float = Field(gt=0, allow_inf_nan=False)
    logged_date: date = Field(default_factory=date.today)


class InventoryItemRequest(BaseModel):
    """Request body accepting free-form pantry category labels."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    quantity: float = Field(gt=0, allow_inf_nan=False)
    category: str
    expiry_date: date

    @field_validator("name")
    @classmethod
    def validate_non_empty_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("name must not be empty")
        return value

    @field_validator("category")
    @classmethod
    def validate_category(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("category must not be empty")
        return value


class GenerateRecipeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_profile: UserProfile
    near_expiry_days: int = Field(default=3, ge=0)


def _json_value(value):
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _fetch_items():
    with closing(sqlite3.connect(database.DEFAULT_DATABASE_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, name, quantity, category, expiry_date
            FROM items
            ORDER BY id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _inventory_records():
    forecast = analyze_inventory(database.DEFAULT_DATABASE_PATH)
    forecast_by_id = {
        int(record["item_id"]): record
        for record in forecast.to_dict(orient="records")
    }
    today = date.today()
    result = []

    for item in _fetch_items():
        expiry_value = item["expiry_date"]
        if not expiry_value:
            freshness_status = "unknown"
        else:
            expiry_date = date.fromisoformat(str(expiry_value))
            days_until_expiry = (expiry_date - today).days
            freshness_status = (
                "expired"
                if days_until_expiry < 0
                else "expiring_soon"
                if days_until_expiry <= 3
                else "fresh"
            )

        record = {
            **item,
            "freshness_status": freshness_status,
            **forecast_by_id.get(item["id"], {}),
        }
        result.append({key: _json_value(value) for key, value in record.items()})
    return result


@app.get("/inventory")
def get_inventory():
    """Return current pantry items with expiry freshness and stock forecasts."""
    return {"items": _inventory_records()}


@app.post("/inventory", status_code=status.HTTP_201_CREATED)
def add_inventory_item(request: InventoryItemRequest):
    """Add a pantry item and return its stored details."""
    with closing(sqlite3.connect(database.DEFAULT_DATABASE_PATH)) as connection:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO items (name, quantity, category, expiry_date)
                VALUES (?, ?, ?, ?)
                """,
                (
                    request.name,
                    request.quantity,
                    request.category,
                    request.expiry_date.isoformat(),
                ),
            )

    return {
        "message": "Pantry item added",
        "item": {
            "id": cursor.lastrowid,
            "name": request.name,
            "quantity": request.quantity,
            "category": request.category,
            "expiry_date": request.expiry_date.isoformat(),
        },
    }


client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

import time

def call_vision_llm_parser(image_bytes: bytes, system_prompt: str) -> list:
    """Helper to send images to Gemini Flash vision model with automatic retry on high demand."""
    if not gemini_client:
        logger.error("Gemini client is not initialized. Check GEMINI_API_KEY in .env.")
        return []

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = gemini_client.models.generate_content(
                model="gemini-3.8-flash",
                contents=[
                    {
                        "inline_data": {
                            "data": base64.b64encode(image_bytes).decode("utf-8"),
                            "mime_type": "image/jpeg"
                        }
                    },
                    system_prompt + " Return ONLY a valid JSON array of objects with keys: name (string), quantity (float), category (string), and optional expiry_date (YYYY-MM-DD format). Do not include markdown code blocks."
                ]
            )
            content = response.text.strip()
            
            if content.startswith("```json"):
                content = content[7:-3].strip()
            elif content.startswith("```"):
                content = content[3:-3].strip()
                
            return json.loads(content)
        except Exception as e:
            logger.warning(f"Gemini Vision attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)  # Wait 2 seconds before retrying
            else:
                logger.error("All Gemini Vision retry attempts failed.")
                return []
    return []



@app.post("/parse-inventory-images", status_code=status.HTTP_201_CREATED)
async def parse_inventory_images(
    source_type: str = Form(...), files: list[UploadFile] = File(...)
):
    """Intelligently parse bills, produce photos, or online screenshots using an LLM."""
    extracted_items = []

    # Tailor system instructions based on the ingestion source
    source_instructions = {
        "Supermarket Bill / Receipt": (
            "You are parsing a grocery store receipt/bill. Extract purchased items,"
            " ignore store metadata, prices, and tax lines. Estimate standard household"
            " quantities and typical Indian pantry categories (Dairy, Grains & Pulses,"
            " Vegetables, Spices, Pantry Staples)."
        ),
        "Farmer's Market Produce Photos": (
            "You are inspecting photos of fresh produce from a farmer's market. Identify"
            " each vegetable/fruit visible, estimate quantities visually, and categorize"
            " them appropriately."
        ),
        "Online Delivery Screenshot (Blinkit/Instamart)": (
            "You are parsing a screenshot of a quick-commerce delivery cart or order"
            " history. Extract item names, ordered quantities, and map them to standard"
            " Indian pantry items."
        ),
    }

    system_prompt = source_instructions.get(
        source_type, "Extract pantry items and quantities."
    )

    # Process images with a multimodal LLM (or Google AI Studio Gemini Flash / OpenAI Vision)
    # For robust multi-modal extraction with free high-tier limits, Google Gemini Flash can be plugged in here:
    for file in files:
        image_bytes = await file.read()
        # Call LLM vision extraction to return strict JSON array:
        # [{"name": "Paneer", "quantity": 2.0, "category": "Dairy", "expiry_date": "2026-10-18"}, ...]
        parsed_batch = call_vision_llm_parser(
            image_bytes, system_prompt
        )  # Helper function
        extracted_items.extend(parsed_batch)

    # Automatically write parsed items to SQLite database
    with closing(sqlite3.connect(database.DEFAULT_DATABASE_PATH)) as connection:
        with connection:
            for item in extracted_items:
                connection.execute(
                    """
                    INSERT INTO items (name, quantity, category, expiry_date)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        item["name"],
                        item["quantity"],
                        item["category"],
                        item.get(
                            "expiry_date",
                            (date.today() + timedelta(days=5)).isoformat(),
                        ),
                    ),
                )

    return {
        "message": "Pantry updated successfully via AI vision extraction",
        "added_items": extracted_items,
    }


@app.get("/shopping-list")
def get_shopping_list():
    """Return pantry items forecast to run low or already out of stock."""
    inventory = analyze_inventory(database.DEFAULT_DATABASE_PATH)
    shopping_list = generate_shopping_list(inventory)
    items = [
        {key: _json_value(value) for key, value in record.items()}
        for record in shopping_list.to_dict(orient="records")
    ]
    return {"items": items}


@app.post("/consume", status_code=status.HTTP_201_CREATED)
def consume_item(request: ConsumeRequest):
    """Record consumption and subtract the consumed amount from pantry stock."""
    if request.logged_date > date.today():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="logged_date cannot be in the future",
        )

    with closing(sqlite3.connect(database.DEFAULT_DATABASE_PATH)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            item = connection.execute(
                "SELECT quantity FROM items WHERE id = ?",
                (request.item_id,),
            ).fetchone()
            if item is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Pantry item {request.item_id} was not found",
                )
            if request.consumed_amount > item[0]:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="consumed_amount cannot exceed the item's current quantity",
                )

            connection.execute(
                "UPDATE items SET quantity = quantity - ? WHERE id = ?",
                (request.consumed_amount, request.item_id),
            )
            cursor = connection.execute(
                """
                INSERT INTO consumption_logs (item_id, consumed_amount, logged_date)
                VALUES (?, ?, ?)
                """,
                (
                    request.item_id,
                    request.consumed_amount,
                    request.logged_date.isoformat(),
                ),
            )

    return {
        "message": "Consumption recorded",
        "log_id": cursor.lastrowid,
        "item_id": request.item_id,
        "consumed_amount": request.consumed_amount,
        "remaining_quantity": item[0] - request.consumed_amount,
        "logged_date": request.logged_date.isoformat(),
    }


@app.post("/generate-recipe")
def generate_recipe(request: GenerateRecipeRequest):
    """Build a recipe prompt using all safe pantry items and expiry priorities."""
    if groq_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Recipe generation is unavailable; configure GROQ_API_KEY in the environment.",
        )

    pantry_items = _fetch_items()
    eligible_items = filter_near_expiry_items(
        pantry_items,
        request.user_profile,
        near_expiry_days=request.near_expiry_days,
    )
    available_items = filter_safe_items(pantry_items, request.user_profile)
    prompt = format_recipe_prompt(
        pantry_items,
        request.user_profile,
        near_expiry_days=request.near_expiry_days,
    )
    
    messages = [{"role": "user", "content": prompt}]
    recipe_response = None

    try:
        try:
            # Active production model on Groq
            completion = groq_client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=messages,
            )
            recipe_response = completion.choices[0].message.content
        except Exception as primary_err:
            logger.warning(
                f"Primary Groq model failed ({primary_err}); retrying with fallback model..."
            )
            # High-capacity fallback model
            completion = groq_client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=messages,
            )
            recipe_response = completion.choices[0].message.content

        if not recipe_response:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The recipe model returned an empty response.",
            )
        recipe_response = re.sub(
            r"<br\s*/?>",
            "\n",
            recipe_response,
            flags=re.IGNORECASE,
        )
            
    except HTTPException:
        raise
    except OpenAIError as error:
        logger.exception("Groq recipe generation failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The recipe provider could not generate a recipe.",
        ) from error

    return {
        "prompt": prompt,
        "available_items": [
            {key: _json_value(value) for key, value in item.items()}
            for item in available_items
        ],
        "eligible_items": [
            {key: _json_value(value) for key, value in item.items()}
            for item in eligible_items
        ],
        "recipe_response": recipe_response,
    }
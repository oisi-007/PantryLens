import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


DietaryRestriction = Literal[
    "vegan",
    "vegetarian",
    "jain",
    "eggetarian",
    "non_vegetarian",
    "keto",
]
Allergy = Literal["nuts", "dairy", "gluten", "sesame", "soy", "shellfish"]

_RESTRICTION_ALIASES = {
    "vegan": "vegan",
    "vegetarian": "vegetarian",
    "jain": "jain",
    "eggetarian": "eggetarian",
    "non vegetarian": "non_vegetarian",
    "keto": "keto",
}
_ALLERGY_ALIASES = {
    "nuts": "nuts",
    "nut": "nuts",
    "peanut": "nuts",
    "peanuts": "nuts",
    "tree nut": "nuts",
    "tree nuts": "nuts",
    "dairy": "dairy",
    "milk": "dairy",
    "gluten": "gluten",
    "wheat": "gluten",
    "sesame": "sesame",
    "til": "sesame",
    "soy": "soy",
    "soya": "soy",
    "shellfish": "shellfish",
}


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dietary_restrictions: list[DietaryRestriction] = Field(default_factory=list)
    allergies: list[Allergy] = Field(default_factory=list)
    excluded_ingredients: list[str] = Field(default_factory=list)

    @field_validator(
        "dietary_restrictions", "allergies", "excluded_ingredients", mode="before"
    )
    @classmethod
    def normalize_profile_values(cls, value, info):
        if value is None or value == "":
            return []
        if isinstance(value, str):
            values = value.split(",")
        elif isinstance(value, list):
            values = value
        else:
            raise ValueError(f"{info.field_name} must be a string or list of strings")

        if info.field_name == "excluded_ingredients":
            normalized_exclusions = []
            for entry in values:
                if not isinstance(entry, str):
                    raise ValueError("Each excluded_ingredients entry must be a string")
                ingredient = re.sub(r"\s+", " ", entry.strip().lower())
                if ingredient:
                    normalized_exclusions.append(ingredient)
            return normalized_exclusions

        aliases = _RESTRICTION_ALIASES
        if info.field_name == "allergies":
            aliases = _ALLERGY_ALIASES
        normalized = []
        for entry in values:
            if not isinstance(entry, str):
                raise ValueError(f"Each {info.field_name} entry must be a string")
            key = re.sub(r"[\s_-]+", " ", entry.strip().lower())
            if not key:
                continue
            if key not in aliases:
                allowed = ", ".join(aliases)
                raise ValueError(
                    f"Unsupported {info.field_name} value {entry!r}; use: {allowed}"
                )
            normalized.append(aliases[key])
        return normalized


_ANIMAL_INGREDIENTS = {
    "anchovy", "bacon", "beef", "chicken", "duck", "fish", "gelatin",
    "ham", "lamb", "meat", "pork", "poultry", "salmon", "sausage",
    "seafood", "shellfish", "turkey",
}
_EGG_INGREDIENTS = {"egg", "eggs"}
_VEGETARIAN_INGREDIENTS = _ANIMAL_INGREDIENTS | _EGG_INGREDIENTS
_MEAT_INGREDIENTS = _ANIMAL_INGREDIENTS
_ANIMAL_DERIVED_INGREDIENTS = _VEGETARIAN_INGREDIENTS | {
    "honey",
}
_VEGAN_INGREDIENTS = {
    "butter", "buttermilk", "casein", "chaas", "cheese", "chhena", "cream",
    "curd", "dahi", "ghee", "khoya", "khoa", "lassi", "milk", "paneer",
    "shrikhand", "whey", "yogurt", "yoghurt",
}
_KETO_HIGH_CARB_INGREDIENTS = {
    "barley", "bread", "cereal", "corn", "flour", "lentil", "lentils",
    "oat", "oats", "pasta", "pea", "peas", "potato", "potatoes", "quinoa",
    "rice", "sugar", "wheat",
}
_ALLERGEN_TERMS = {
    "nuts": {
        "almond", "almonds", "cashew", "cashews", "hazelnut", "hazelnuts",
        "macadamia", "peanut", "peanuts", "pecan", "pecans", "pistachio",
        "pistachios", "walnut", "walnuts",
    },
    "dairy": {
        "butter", "buttermilk", "casein", "chaas", "cheese", "chhena", "cream",
        "curd", "dahi", "ghee", "khoya", "khoa", "lassi", "milk", "paneer",
        "shrikhand", "whey", "yogurt", "yoghurt",
    },
    "gluten": {
        "barley", "bread", "bulgur", "couscous", "malt", "pasta", "rye",
        "seitan", "semolina", "spelt", "wheat",
    },
    "sesame": {"sesame", "tahini", "til"},
    "soy": {"soy", "soya", "tofu", "tempeh"},
    "shellfish": {"crab", "lobster", "prawn", "prawns", "shrimp", "shrimp paste"},
}


def _contains_term(text, terms):
    normalized_text = re.sub(r"[_-]+", " ", text.lower())
    return any(
        re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized_text)
        for term in terms
    )


def _item_text(item):
    parts = []
    for key in ("name", "category", "ingredients"):
        value = item.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            parts.extend(str(part) for part in value)
    return " ".join(parts)


def _validated_profile(profile):
    if isinstance(profile, UserProfile):
        return profile
    if isinstance(profile, Mapping):
        return UserProfile.model_validate(profile)
    raise TypeError("user_profile must be a UserProfile or mapping")


def filter_safe_items(items, user_profile):
    """Filter pantry items that conflict with a user's diet or allergies."""
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise TypeError("items must be a sequence of item mappings")
    profile = _validated_profile(user_profile)

    safe_items = []
    for item in items:
        if not isinstance(item, Mapping):
            raise TypeError("each pantry item must be a mapping")
        text = _item_text(item)
        unsafe = any(
            _contains_term(text, _ALLERGEN_TERMS[allergy])
            for allergy in profile.allergies
        )

        restrictions = set(profile.dietary_restrictions)
        if "vegan" in restrictions:
            unsafe = unsafe or _contains_term(
                text, _ANIMAL_DERIVED_INGREDIENTS | _VEGAN_INGREDIENTS
            )
        elif "jain" in restrictions or "vegetarian" in restrictions:
            unsafe = unsafe or _contains_term(text, _VEGETARIAN_INGREDIENTS)
        elif "eggetarian" in restrictions:
            unsafe = unsafe or _contains_term(text, _MEAT_INGREDIENTS)

        if "jain" in restrictions:
            unsafe = unsafe or _contains_term(text, {"onion", "garlic"})

        if profile.excluded_ingredients:
            unsafe = unsafe or _contains_term(text, set(profile.excluded_ingredients))

        if "keto" in restrictions:
            unsafe = unsafe or _contains_term(text, _KETO_HIGH_CARB_INGREDIENTS)

        if not unsafe:
            safe_items.append(item)
    return safe_items


def filter_near_expiry_items(
    items,
    user_profile,
    near_expiry_days=3,
    as_of_date=None,
):
    """Return near-expiry pantry items that satisfy a user's dietary rules."""
    if not isinstance(near_expiry_days, int) or isinstance(near_expiry_days, bool):
        raise TypeError("near_expiry_days must be an integer")
    if near_expiry_days < 0:
        raise ValueError("near_expiry_days must be non-negative")
    as_of_date = as_of_date or date.today()
    if not isinstance(as_of_date, date) or isinstance(as_of_date, datetime):
        raise TypeError("as_of_date must be a datetime.date")

    cutoff_date = as_of_date + timedelta(days=near_expiry_days)
    near_expiry_items = []
    for item in filter_safe_items(items, user_profile):
        expiry_value = item.get("expiry_date")
        if not expiry_value:
            continue
        try:
            if isinstance(expiry_value, datetime):
                expiry_date = expiry_value.date()
            elif isinstance(expiry_value, date):
                expiry_date = expiry_value
            else:
                expiry_date = date.fromisoformat(str(expiry_value))
        except ValueError as error:
            raise ValueError(
                f"Invalid expiry_date for item {item.get('name', '<unnamed>')!r}: "
                f"{expiry_value!r}"
            ) from error
        if as_of_date <= expiry_date <= cutoff_date:
            near_expiry_items.append(item)
    return near_expiry_items


def format_recipe_prompt(
    items,
    user_profile,
    near_expiry_days=3,
    as_of_date=None,
):
    """Format safe pantry inventory with near-expiry priority metadata."""
    profile = _validated_profile(user_profile)
    near_expiry_items = filter_near_expiry_items(
        items,
        profile,
        near_expiry_days=near_expiry_days,
        as_of_date=as_of_date,
    )
    as_of_date = as_of_date or date.today()
    near_expiry_ids = {
        item.get("id")
        for item in near_expiry_items
        if item.get("id") is not None
    }
    cutoff_date = as_of_date + timedelta(days=near_expiry_days)
    prompt_items = []
    for item in filter_safe_items(items, profile):
        prompt_item = {
            key: item.get(key)
            for key in ("id", "name", "quantity", "category", "expiry_date")
            if key in item
        }
        expiry_value = prompt_item.get("expiry_date")
        if isinstance(expiry_value, datetime):
            expiry_date = expiry_value.date()
            prompt_item["expiry_date"] = expiry_value.isoformat()
        elif isinstance(expiry_value, date):
            expiry_date = expiry_value
            prompt_item["expiry_date"] = expiry_value.isoformat()
        elif expiry_value:
            expiry_date = date.fromisoformat(str(expiry_value))
        else:
            expiry_date = None

        days_until_expiry = (
            (expiry_date - as_of_date).days if expiry_date is not None else None
        )
        if expiry_date is None:
            expiry_status = "unknown"
        elif expiry_date < as_of_date:
            expiry_status = "expired"
        elif expiry_date <= cutoff_date:
            expiry_status = "near_expiry"
        else:
            expiry_status = "not_near_expiry"
        prompt_item["days_until_expiry"] = days_until_expiry
        prompt_item["expiry_status"] = expiry_status
        item_id = item.get("id")
        prompt_item["prioritize_for_use"] = (
            expiry_status == "near_expiry"
            and (item_id is None or item_id in near_expiry_ids)
        )
        prompt_items.append(prompt_item)

    prompt_data = {
        "as_of_date": as_of_date.isoformat(),
        "near_expiry_window_days": near_expiry_days,
        "dietary_restrictions": profile.dietary_restrictions,
        "allergies": profile.allergies,
        "excluded_ingredients": profile.excluded_ingredients,
        "available_pantry_items": prompt_items,
    }
    return (
        "You are a creative recipe assistant. Create complete, practical, delicious "
        "recipes from a diverse range of global and regional cuisines, including "
        "Continental, Italian, Chinese, American, Indian, and fusion dishes. The "
        "available pantry list includes all ingredients that may be used; do not "
        "limit a recipe to only the near-expiry items. Prioritize ingredients marked "
        "prioritize_for_use or near_expiry, and combine them freely with other "
        "available pantry items such as flour, rice, spices, and vegetables. Never "
        "use an ingredient marked expired, with zero or insufficient quantity, or "
        "one that violates a dietary restriction, allergy, or exclusion. Do not "
        "invent unavailable ingredients, spices, or garnishes. Respect all dietary "
        "rules and use only ingredients that are safe and available in the pantry. "
        "If no suitable dish can be made, explain why instead of suggesting unsafe "
        "substitutions. For each recipe, provide a name, ingredients with amounts, "
        "and concise preparation steps.\n"
        "Recipe request data (JSON):\n"
        f"{json.dumps(prompt_data, indent=2)}"
    )
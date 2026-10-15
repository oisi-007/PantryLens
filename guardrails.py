"""Compatibility imports for the original ``gaurdrails`` module filename."""

from gaurdrails import (
    Allergy,
    DietaryRestriction,
    UserProfile,
    filter_near_expiry_items,
    filter_safe_items,
    format_recipe_prompt,
)

__all__ = [
    "Allergy",
    "DietaryRestriction",
    "UserProfile",
    "filter_near_expiry_items",
    "filter_safe_items",
    "format_recipe_prompt",
]

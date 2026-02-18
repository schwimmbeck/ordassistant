"""Pydantic models for structured LLM output in the ORD pipeline."""

from typing import Literal

from pydantic import BaseModel, Field


class IntentClassification(BaseModel):
    """Classifies user intent as generation or question."""

    intent: Literal["generate", "question"] = Field(
        description="'generate' if user wants ORD circuit code, 'question' if asking about ORD"
    )


class LayoutChange(BaseModel):
    """A single layout modification for a port or instance."""

    element_name: str = Field(
        description="Name of the port or instance to modify (e.g., 'vdd', 'pd', 'm_tail')"
    )

    new_pos_x: int | None = Field(
        default=None,
        description="New X coordinate, or null to keep current",
    )

    new_pos_y: int | None = Field(
        default=None,
        description="New Y coordinate, or null to keep current",
    )

    new_alignment: str | None = Field(
        default=None,
        description="New alignment direction: 'North', 'South', 'East', or 'West'. Null to keep current.",
    )

    disable_route: bool = Field(
        default=False,
        description="Set to true to add .route = False for this element",
    )


class LayoutFixPlan(BaseModel):
    """Structured plan for fixing schematic layout issues."""

    reasoning: str = Field(
        description="Brief explanation of why these changes fix the reported issues"
    )

    changes: list[LayoutChange] = Field(
        description="List of layout changes to apply to ports and instances"
    )

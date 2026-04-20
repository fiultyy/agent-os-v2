"""Template engine for prompt rendering."""

import re
from typing import Any


class PromptTemplate:
    """Renders prompt templates with variable substitution."""

    def __init__(self, template: str, variables: dict[str, Any] | None = None):
        self.template = template
        self.variables = variables or {}

    def render(self, **kwargs: Any) -> str:
        """Render the template with provided variables."""
        all_vars = {**self.variables, **kwargs}
        # Convert {{var}} → {var} for str.format compatibility
        converted = re.sub(r"\{\{(\w+)\}\}", r"{\1}", self.template)
        return converted.format(**all_vars)

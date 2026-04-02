"""Template engine for prompt rendering."""

from typing import Any


class PromptTemplate:
    """Renders prompt templates with variable substitution."""

    def __init__(self, template: str, variables: dict[str, Any] | None = None):
        self.template = template
        self.variables = variables or {}

    def render(self, **kwargs: Any) -> str:
        """Render the template with provided variables."""
        all_vars = {**self.variables, **kwargs}
        return self.template.format(**all_vars)

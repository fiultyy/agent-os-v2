"""
farewell - 告别工具

与 greet 配对的示例工具。
"""


def farewell(name: str = "World", language: str = "en") -> dict:
    """
    告别函数

    Args:
        name: 告别对象名称
        language: 语言

    Returns:
        {
            "success": bool,
            "message": str,
            "error": str (if failed)
        }

    Example:
        >>> result = farewell("Alice")
        >>> print(result["message"])  # "Goodbye, Alice!"
    """
    farewells = {
        "en": f"Goodbye, {name}!",
        "zh": f"再见，{name}！",
        "ja": f"さようなら、{name}！",
        "es": f"¡Adiós, {name}!",
        "fr": f"Au revoir, {name}！",
    }

    message = farewells.get(language, farewells["en"])

    return {
        "success": True,
        "message": message,
        "name": name,
        "language": language,
    }
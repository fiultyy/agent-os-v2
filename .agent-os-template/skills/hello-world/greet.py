"""
greet - 问候工具

简单的示例工具，展示 Skill 工具的标准签名格式。
"""


def greet(name: str = "World", language: str = "en") -> dict:
    """
    问候函数

    Args:
        name: 被问候的对象名称
        language: 语言，默认英语

    Returns:
        {
            "success": bool,
            "message": str,
            "error": str (if failed)
        }

    Example:
        >>> result = greet("Alice")
        >>> print(result["message"])  # "Hello, Alice!"
    """
    greetings = {
        "en": f"Hello, {name}!",
        "zh": f"你好，{name}！",
        "ja": f"こんにちは、{name}！",
        "es": f"¡Hola, {name}!",
        "fr": f"Bonjour, {name}！",
    }

    message = greetings.get(language, greetings["en"])

    return {
        "success": True,
        "message": message,
        "name": name,
        "language": language,
    }
"""Unit tests for L3.4 ToolCatalog."""

from __future__ import annotations


from src.tools.catalog import ToolCatalog, ToolCatalogAPI, ToolCatalogEntry, ToolLayer


class TestToolCatalogEntry:
    """Tests for ToolCatalogEntry."""

    def test_matches_tags_partial(self):
        entry = ToolCatalogEntry(
            name="test_tool",
            layer=ToolLayer.SKILL,
            category="code",
            tags=["browser", "automation"],
        )
        assert entry.matches_tags(["browser"]) is True
        assert entry.matches_tags(["code"]) is False  # not a tag
        assert entry.matches_tags(["browser", "http"]) is True
        assert entry.matches_tags(["sql"]) is False

    def test_matches_tags_empty(self):
        entry = ToolCatalogEntry(name="x", layer=ToolLayer.PRIMITIVE, category="y")
        assert entry.matches_tags([]) is False


class TestToolCatalog:
    """Tests for ToolCatalog core logic."""

    def test_register_and_find(self):
        cat = ToolCatalog()
        entry = ToolCatalogEntry(
            name="my_tool", layer=ToolLayer.SKILL, category="test"
        )
        cat.register(entry)
        assert cat.find("my_tool") is entry
        assert cat.find("missing") is None

    def test_unregister(self):
        cat = ToolCatalog()
        entry = ToolCatalogEntry(name="t", layer=ToolLayer.SKILL, category="c")
        cat.register(entry)
        removed = cat.unregister("t")
        assert removed is entry
        assert cat.find("t") is None
        assert cat.unregister("nope") is None

    def test_list_all(self):
        cat = ToolCatalog()
        for i in range(3):
            cat.register(
                ToolCatalogEntry(name=f"t{i}", layer=ToolLayer.SKILL, category="c")
            )
        results = cat.list_all()
        assert len(results) == 3
        names = {e.name for e in results}
        assert names == {"t0", "t1", "t2"}

    def test_list_by_layer(self):
        cat = ToolCatalog()
        cat.register(
            ToolCatalogEntry(name="p", layer=ToolLayer.PRIMITIVE, category="x")
        )
        cat.register(
            ToolCatalogEntry(name="s", layer=ToolLayer.SKILL, category="x")
        )
        cat.register(
            ToolCatalogEntry(name="c", layer=ToolLayer.COMPOSITE, category="x")
        )
        assert {e.name for e in cat.list_by_layer(ToolLayer.PRIMITIVE)} == {"p"}
        assert {e.name for e in cat.list_by_layer(ToolLayer.SKILL)} == {"s"}
        assert {e.name for e in cat.list_by_layer(ToolLayer.COMPOSITE)} == {"c"}

    def test_list_by_category(self):
        cat = ToolCatalog()
        cat.register(
            ToolCatalogEntry(name="t1", layer=ToolLayer.SKILL, category="browser")
        )
        cat.register(
            ToolCatalogEntry(name="t2", layer=ToolLayer.SKILL, category="code")
        )
        cat.register(
            ToolCatalogEntry(name="t3", layer=ToolLayer.SKILL, category="browser")
        )
        browser = cat.list_by_category("browser")
        assert {e.name for e in browser} == {"t1", "t3"}
        assert cat.list_by_category("missing") == []

    def test_list_by_tags(self):
        cat = ToolCatalog()
        cat.register(
            ToolCatalogEntry(
                name="t1", layer=ToolLayer.SKILL, category="c", tags=["http", "api"]
            )
        )
        cat.register(
            ToolCatalogEntry(
                name="t2", layer=ToolLayer.SKILL, category="c", tags=["sql"]
            )
        )
        cat.register(
            ToolCatalogEntry(
                name="t3", layer=ToolLayer.SKILL, category="c", tags=["http", "browser"]
            )
        )
        assert {e.name for e in cat.list_by_tags(["http"])} == {"t1", "t3"}
        assert {e.name for e in cat.list_by_tags(["sql"])} == {"t2"}
        assert {e.name for e in cat.list_by_tags(["http", "sql"])} == {"t1", "t2", "t3"}
        assert cat.list_by_tags(["missing"]) == []

    def test_list_versions(self):
        cat = ToolCatalog()
        entry = ToolCatalogEntry(
            name="my_tool", layer=ToolLayer.SKILL, category="c", version="2.0.0"
        )
        cat.register(entry)
        assert cat.list_versions("my_tool") == ["2.0.0"]
        assert cat.list_versions("nonexistent") == []

    def test_search(self):
        cat = ToolCatalog()
        cat.register(
            ToolCatalogEntry(
                name="browser_open",
                layer=ToolLayer.SKILL,
                category="browser",
                description="Opens a URL in browser",
            )
        )
        cat.register(
            ToolCatalogEntry(
                name="sql_query",
                layer=ToolLayer.SKILL,
                category="database",
                description="Executes SQL",
            )
        )
        cat.register(
            ToolCatalogEntry(
                name="http_get",
                layer=ToolLayer.SKILL,
                category="browser",
                description="HTTP GET request",
            )
        )
        assert {e.name for e in cat.search("browser")} == {"browser_open", "http_get"}
        assert {e.name for e in cat.search("sql")} == {"sql_query"}
        assert {e.name for e in cat.search("open")} == {"browser_open"}
        assert cat.search("notexist") == []

    def test_count(self):
        cat = ToolCatalog()
        assert cat.count() == 0
        for i in range(5):
            cat.register(
                ToolCatalogEntry(name=f"t{i}", layer=ToolLayer.SKILL, category="c")
            )
        assert cat.count() == 5


class TestToolCatalogAPI:
    """Tests for ToolCatalogAPI serialization."""

    def test_to_dict(self):
        cat = ToolCatalog()
        cat.register(
            ToolCatalogEntry(
                name="my_tool",
                layer=ToolLayer.SKILL,
                category="code",
                tags=["lint"],
                version="1.2.0",
                description="Lints code",
            )
        )
        api = ToolCatalogAPI(cat)
        d = api.to_dict()
        assert d["total"] == 1
        assert len(d["tools"]) == 1
        assert d["tools"][0]["name"] == "my_tool"
        assert d["tools"][0]["layer"] == "L3.2"
        assert d["tools"][0]["tags"] == ["lint"]

    def test_filter_dict_by_layer(self):
        cat = ToolCatalog()
        cat.register(
            ToolCatalogEntry(name="p", layer=ToolLayer.PRIMITIVE, category="x")
        )
        cat.register(
            ToolCatalogEntry(name="s", layer=ToolLayer.SKILL, category="x")
        )
        api = ToolCatalogAPI(cat)
        d = api.filter_dict(layer="L3.1")
        assert d["total"] == 1
        assert d["tools"][0]["name"] == "p"

    def test_filter_dict_by_category(self):
        cat = ToolCatalog()
        cat.register(
            ToolCatalogEntry(name="t1", layer=ToolLayer.SKILL, category="browser")
        )
        cat.register(
            ToolCatalogEntry(name="t2", layer=ToolLayer.SKILL, category="code")
        )
        api = ToolCatalogAPI(cat)
        d = api.filter_dict(category="browser")
        assert d["total"] == 1
        assert d["tools"][0]["name"] == "t1"

    def test_filter_dict_by_tags(self):
        cat = ToolCatalog()
        cat.register(
            ToolCatalogEntry(
                name="t1", layer=ToolLayer.SKILL, category="c", tags=["http"]
            )
        )
        cat.register(
            ToolCatalogEntry(name="t2", layer=ToolLayer.SKILL, category="c", tags=["sql"])
        )
        api = ToolCatalogAPI(cat)
        d = api.filter_dict(tags=["http"])
        assert d["total"] == 1
        assert d["tools"][0]["name"] == "t1"

    def test_filter_dict_combined(self):
        cat = ToolCatalog()
        cat.register(
            ToolCatalogEntry(
                name="t1",
                layer=ToolLayer.SKILL,
                category="browser",
                tags=["http"],
            )
        )
        cat.register(
            ToolCatalogEntry(
                name="t2",
                layer=ToolLayer.COMPOSITE,
                category="browser",
                tags=["http"],
            )
        )
        cat.register(
            ToolCatalogEntry(
                name="t3",
                layer=ToolLayer.SKILL,
                category="browser",
                tags=["sql"],
            )
        )
        api = ToolCatalogAPI(cat)
        d = api.filter_dict(layer="L3.2", category="browser", tags=["http"])
        assert d["total"] == 1
        assert d["tools"][0]["name"] == "t1"

    def test_filter_dict_no_results(self):
        cat = ToolCatalog()
        cat.register(
            ToolCatalogEntry(name="t1", layer=ToolLayer.SKILL, category="x")
        )
        api = ToolCatalogAPI(cat)
        d = api.filter_dict(layer="L3.3")
        assert d["total"] == 0
        assert d["tools"] == []


class TestToolRegistryIntegration:
    """Integration: ToolRegistry automatically populates the catalog."""

    def test_register_creates_catalog_entry(self):
        from src.tools.registry import ToolRegistry

        registry = ToolRegistry()
        assert isinstance(registry.get_catalog(), ToolCatalog)

        def handler(x: int) -> int:
            return x + 1

        registry.register(
            name="increment",
            handler=handler,
            description="Adds one",
            layer=ToolLayer.PRIMITIVE,
            category="math",
            tags=["arithmetic"],
            version="1.0.0",
        )

        # catalog should reflect the registration
        cat = registry.get_catalog()
        entry = cat.find("increment")
        assert entry is not None
        assert entry.layer == ToolLayer.PRIMITIVE
        assert entry.category == "math"
        assert entry.tags == ["arithmetic"]
        assert entry.version == "1.0.0"
        assert entry.description == "Adds one"

    def test_register_multiple_layers(self):
        from src.tools.registry import ToolRegistry

        registry = ToolRegistry()

        registry.register(name="prim", handler=lambda: None, layer=ToolLayer.PRIMITIVE)
        registry.register(name="skill", handler=lambda: None, layer=ToolLayer.SKILL)
        registry.register(name="comp", handler=lambda: None, layer=ToolLayer.COMPOSITE)

        assert {e.name for e in registry.get_catalog().list_by_layer(ToolLayer.PRIMITIVE)} == {"prim"}
        assert {e.name for e in registry.get_catalog().list_by_layer(ToolLayer.SKILL)} == {"skill"}
        assert {e.name for e in registry.get_catalog().list_by_layer(ToolLayer.COMPOSITE)} == {"comp"}

    def test_list_by_category_via_registry_catalog(self):
        from src.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(name="t1", handler=lambda: None, category="browser")
        registry.register(name="t2", handler=lambda: None, category="code")

        results = registry.get_catalog().list_by_category("browser")
        assert [e.name for e in results] == ["t1"]

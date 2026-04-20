"""D-19 CA Coding 场景特化 - 单元测试"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from context.coding_context import (
    RiskLevel, ToolRiskMetadata, CodingLayer1, CodingLayer2,
    CodingLayer3, CodingLayer4, CAContextCoding
)
from context.codebase_context import CodebaseContextBuilder
from context.git_context import GitContextProvider


class TestRiskLevel:
    def test_risk_level_enum(self):
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.HIGH.value == "high"


class TestCodingLayers:
    def test_coding_layer1(self):
        layer1 = CodingLayer1(tools=[
            {"name": "read_file", "risk_level": RiskLevel.LOW}
        ])
        assert len(layer1.tools) == 1
    
    def test_coding_layer2(self):
        layer2 = CodingLayer2()
        assert "coding agent" in layer2.soul.lower()
    
    def test_coding_layer3(self):
        layer3 = CodingLayer3(project_structure="src/\n  main.py")
        assert layer3.project_structure == "src/\n  main.py"
    
    def test_coding_layer4(self):
        layer4 = CodingLayer4(git_status="M src/main.py")
        assert layer4.git_status == "M src/main.py"


class TestCAContextCoding:
    def test_compile_with_tools(self):
        """L1 有内容时应该包含 L1"""
        layer1 = CodingLayer1(tools=[
            {"name": "read_file", "risk_level": RiskLevel.LOW}
        ])
        layer2 = CodingLayer2()
        layer3 = CodingLayer3()
        layer4 = CodingLayer4()
        layer6 = type('Layer6', (), {'compile': lambda self: ''})()
        
        ctx = CAContextCoding(
            layer1=layer1,
            layer2=layer2,
            layer3=layer3,
            layer4=layer4,
            layer6=layer6
        )
        
        compiled = ctx.compile()
        assert "L1" in compiled or "L2" in compiled
    
    def test_compile_contains_rules(self):
        layer1 = CodingLayer1(tools=[])
        layer2 = CodingLayer2()
        layer3 = CodingLayer3()
        layer4 = CodingLayer4()
        layer6 = type('Layer6', (), {'compile': lambda self: ''})()
        
        ctx = CAContextCoding(
            layer1=layer1,
            layer2=layer2,
            layer3=layer3,
            layer4=layer4,
            layer6=layer6
        )
        
        compiled = ctx.compile()
        assert "Plan before code" in compiled or "coding agent" in compiled


class TestCodebaseContextBuilder:
    def test_build_structure_empty(self, tmp_path):
        builder = CodebaseContextBuilder(str(tmp_path))
        result = builder.build_project_structure()
        assert "(empty)" in result or result == ""
    
    def test_ignore_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "main.py").touch()
        
        builder = CodebaseContextBuilder(str(tmp_path))
        result = builder.build_project_structure()
        assert "main.py" in result
        assert ".git" not in result
        assert "__pycache__" not in result


class TestGitContextProvider:
    def test_init(self, tmp_path):
        provider = GitContextProvider(str(tmp_path))
        assert provider.repo == str(tmp_path)
    
    def test_get_status_non_repo(self, tmp_path):
        provider = GitContextProvider(str(tmp_path))
        status = provider.get_status()
        assert status == ""
    
    def test_get_branch_non_repo(self, tmp_path):
        provider = GitContextProvider(str(tmp_path))
        branch = provider.get_branch()
        assert branch == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestCodingLayer6:
    """L6: 输出格式化测试"""
    
    def test_format_diff(self):
        from context.coding_context import CodingLayer6
        layer6 = CodingLayer6()
        diff_text = layer6.format_diff("+ new line\n- old line")
        assert "+ new line" in diff_text
        assert "- old line" in diff_text
    
    def test_format_test_result(self):
        from context.coding_context import CodingLayer6
        layer6 = CodingLayer6()
        results = [{"name": "test_foo", "status": "passed"}, {"name": "test_bar", "status": "passed"}]
        result = layer6.format_test_result(results)
        assert "2/2 passed" in result


class TestToolRiskMetadata:
    """ToolRiskMetadata 数据类测试"""
    
    def test_tool_risk_metadata(self):
        from context.coding_context import ToolRiskMetadata, RiskLevel
        metadata = ToolRiskMetadata(
            risk_level=RiskLevel.HIGH,
            sideline_required=True,
            requires_confirmation=True,
            undoable=False
        )
        assert metadata.risk_level == RiskLevel.HIGH
        assert metadata.sideline_required == True
        assert metadata.requires_confirmation == True
        assert metadata.undoable == False

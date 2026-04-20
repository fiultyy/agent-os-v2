# tests/unit/test_pitfail.py
import pytest
import tempfile
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from pitfail.models import PitfallRecord
from pitfail.registry import PitfailRegistry


class TestPitfallRecord:
    def test_create_record(self):
        record = PitfallRecord(
            file_path="test.py",
            error_type="ValueError",
            symptom="test symptom",
            root_cause="test root cause",
            fix="test fix"
        )
        assert record.file_path == "test.py"
        assert record.error_type == "ValueError"
        assert record.recurrence_count == 1
        assert record.project_id == "default"


class TestPitfailRegistry:
    def setup_method(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.registry = PitfailRegistry(db_path=self.db_path)

    def teardown_method(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_record_creates_successfully(self):
        pitfall = PitfallRecord(
            file_path="test.py",
            error_type="ValueError",
            symptom="Invalid value",
            root_cause="Missing validation",
            fix="Add validation"
        )
        pid = self.registry.record(pitfall)
        assert pid is not None
        fetched = self.registry.get(pid)
        assert fetched is not None
        assert fetched.file_path == "test.py"
        assert fetched.error_type == "ValueError"

    def test_search_matches_successfully(self):
        pitfall1 = PitfallRecord(
            file_path="app.py",
            error_type="KeyError",
            symptom="Missing key in dict",
            root_cause="Key not found",
            fix="Use .get() method"
        )
        pitfall2 = PitfallRecord(
            file_path="app.py",
            error_type="ValueError",
            symptom="Invalid type",
            root_cause="Type mismatch",
            fix="Cast to int"
        )
        self.registry.record(pitfall1)
        self.registry.record(pitfall2)

        results = self.registry.search("key")
        assert len(results) >= 1
        assert any(r.error_type == "KeyError" for r in results)

    def test_match_exact_match_successfully(self):
        pitfall1 = PitfallRecord(
            file_path="utils.py",
            error_type="TypeError",
            symptom="Cannot add str and int",
            root_cause="Type error",
            fix="Convert to string"
        )
        pitfall2 = PitfallRecord(
            file_path="utils.py",
            error_type="ValueError",
            symptom="Invalid value",
            root_cause="Out of range",
            fix="Check bounds"
        )
        self.registry.record(pitfall1)
        self.registry.record(pitfall2)

        results = self.registry.match("utils.py", "TypeError")
        assert len(results) == 1
        assert results[0].error_type == "TypeError"
        assert results[0].file_path == "utils.py"

    def test_increment_recurrence(self):
        pitfall = PitfallRecord(
            file_path="test.py",
            error_type="RuntimeError",
            symptom="Out of memory",
            root_cause="Memory leak",
            fix="Clear cache"
        )
        pid = self.registry.record(pitfall)
        original = self.registry.get(pid)
        assert original.recurrence_count == 1

        self.registry.increment_recurrence(pid)
        updated = self.registry.get(pid)
        assert updated.recurrence_count == 2

    def test_list_all(self):
        for i in range(3):
            self.registry.record(PitfallRecord(
                file_path=f"file{i}.py",
                error_type="Error",
                symptom="Symptom",
                root_cause="Cause",
                fix="Fix"
            ))
        results = self.registry.list_all()
        assert len(results) == 3

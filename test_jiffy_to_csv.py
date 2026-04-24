"""
Unit tests for jiffy_to_csv.py
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import jiffy_to_csv as app


# Timezone for reference
BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture
def sample_json_data():
    """Minimal mock JSON fixture with a known structure."""
    return {
        "time_owners": [
            {
                "id": "owner-ORBIT",
                "name": "ORBIT",
                "parent_id": None,
                "status": "ACTIVE",
            },
            {
                "id": "owner-ORBIT-5001",
                "name": "ORBIT-5001",
                "parent_id": "owner-ORBIT",
                "status": "ACTIVE",
            },
            {
                "id": "owner-meeting",
                "name": "Meeting",
                "parent_id": "owner-ORBIT",
                "status": "ACTIVE",
            },
            {
                "id": "owner-pause",
                "name": "Pause",
                "parent_id": None,
                "status": "ACTIVE",
            },
        ],
        "time_entries": [
            # 2024-03-01: ORBIT entry without note (should use fallback to "ORBIT")
            {
                "id": "entry-1",
                "owner_id": "owner-ORBIT",
                "start_time": 1709279400000,  # 2024-03-01 08:30 Berlin time
                "stop_time": 1709283000000,   # 2024-03-01 09:30 Berlin time (30 min)
                "note": "",
                "status": "ACTIVE",
            },
            # 2024-03-01: ORBIT-5001 entry with note
            {
                "id": "entry-2",
                "owner_id": "owner-ORBIT-5001",
                "start_time": 1709283600000,  # 2024-03-01 09:45 Berlin time
                "stop_time": 1709287200000,   # 2024-03-01 10:45 Berlin time (1 hour)
                "note": "Task 001",
                "status": "ACTIVE",
            },
            # 2024-03-01: Meeting entry with note
            {
                "id": "entry-3",
                "owner_id": "owner-meeting",
                "start_time": 1709288400000,  # 2024-03-01 11:00 Berlin time
                "stop_time": 1709292000000,   # 2024-03-01 12:00 Berlin time (1 hour)
                "note": "Sprint Planning",
                "status": "ACTIVE",
            },
            # 2024-03-04: Multiple entries
            {
                "id": "entry-4",
                "owner_id": "owner-ORBIT",
                "start_time": 1709544600000,  # 2024-03-04 09:00 Berlin time
                "stop_time": 1709548200000,   # 2024-03-04 10:00 Berlin time (1 hour)
                "note": "",
                "status": "ACTIVE",
            },
            {
                "id": "entry-5",
                "owner_id": "owner-meeting",
                "start_time": 1709548200000,  # 2024-03-04 10:00 Berlin time
                "stop_time": 1709551800000,   # 2024-03-04 11:00 Berlin time (1 hour)
                "note": "",
                "status": "ACTIVE",
            },
            # Pause entry (should be marked as pause)
            {
                "id": "entry-pause",
                "owner_id": "owner-pause",
                "start_time": 1709551800000,  # 2024-03-04 11:00 Berlin time
                "stop_time": 1709553600000,   # 2024-03-04 11:30 Berlin time (30 min pause)
                "note": "Lunch break",
                "status": "ACTIVE",
            },
            # Deleted entry (should be skipped)
            {
                "id": "entry-deleted",
                "owner_id": "owner-ORBIT",
                "start_time": 1709560000000,
                "stop_time": 1709563600000,
                "note": "Deleted entry",
                "status": "DELETED",
            },
        ],
    }


@pytest.fixture
def temp_output_dir():
    """Create a temporary output directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestJSONLoading:
    def test_load_json(self, sample_json_data, tmp_path):
        """Test JSON loading."""
        json_file = tmp_path / "test.json"
        json_file.write_text(json.dumps(sample_json_data), encoding="utf-8")

        data = app.load_json(json_file)
        assert len(data["time_owners"]) == 4
        assert len(data["time_entries"]) == 7

    def test_build_owner_map(self, sample_json_data):
        """Test owner map building."""
        owners = app.build_owner_map(sample_json_data)
        assert len(owners) == 4
        assert owners["owner-ORBIT"].name == "ORBIT"
        assert owners["owner-ORBIT"].parent_id is None
        assert owners["owner-ORBIT-5001"].parent_id == "owner-ORBIT"

    def test_build_entries(self, sample_json_data):
        """Test entry list building."""
        entries = app.build_entries(sample_json_data)
        # Should include all entries (ACTIVE and DELETED)
        assert len(entries) == 7
        # But filter_and_round will skip DELETED entries
        assert any(e.status == "DELETED" for e in entries)


class TestTimeRounding:
    def test_floor_15(self):
        """Test floor_15 rounding."""
        # 08:37 should round down to 08:30
        dt = datetime(2024, 3, 1, 8, 37, 0, tzinfo=BERLIN)
        rounded = app.floor_15(dt)
        assert rounded.hour == 8
        assert rounded.minute == 30

    def test_ceil_15(self):
        """Test ceil_15 rounding."""
        # 08:37 should round up to 08:45
        dt = datetime(2024, 3, 1, 8, 37, 0, tzinfo=BERLIN)
        rounded = app.ceil_15(dt)
        assert rounded.hour == 8
        assert rounded.minute == 45


class TestDescriptionBuilding:
    def test_description_primary_root_with_note(self, sample_json_data):
        """Description with note on primary root should return the note."""
        owners = app.build_owner_map(sample_json_data)
        entry = app.ProcessedEntry(
            date="01.03.2024",
            date_iso="2024-03-01",
            owner_id="owner-ORBIT",
            owner_name="ORBIT",
            start_dt=datetime(2024, 3, 1, 8, 30, tzinfo=BERLIN),
            stop_dt=datetime(2024, 3, 1, 9, 0, tzinfo=BERLIN),
            duration_h=0.5,
            note="My task",
            is_pause=False,
        )
        owner = owners["owner-ORBIT"]
        desc = app.build_description(
            entry, owner, "owner-ORBIT", "ORBIT", owners
        )
        assert desc == "My task"

    def test_description_primary_root_empty_fallback(self, sample_json_data):
        """Description with empty note on primary root should use fallback."""
        owners = app.build_owner_map(sample_json_data)
        entry = app.ProcessedEntry(
            date="01.03.2024",
            date_iso="2024-03-01",
            owner_id="owner-ORBIT",
            owner_name="ORBIT",
            start_dt=datetime(2024, 3, 1, 8, 30, tzinfo=BERLIN),
            stop_dt=datetime(2024, 3, 1, 9, 0, tzinfo=BERLIN),
            duration_h=0.5,
            note="",
            is_pause=False,
        )
        owner = owners["owner-ORBIT"]
        desc = app.build_description(
            entry, owner, "owner-ORBIT", "ORBIT", owners
        )
        assert desc == "ORBIT"

    def test_description_subproject_with_note(self, sample_json_data):
        """Description with note on sub-project should return project name – note."""
        owners = app.build_owner_map(sample_json_data)
        entry = app.ProcessedEntry(
            date="01.03.2024",
            date_iso="2024-03-01",
            owner_id="owner-ORBIT-5001",
            owner_name="ORBIT-5001",
            start_dt=datetime(2024, 3, 1, 9, 45, tzinfo=BERLIN),
            stop_dt=datetime(2024, 3, 1, 10, 45, tzinfo=BERLIN),
            duration_h=1.0,
            note="Task 001",
            is_pause=False,
        )
        owner = owners["owner-ORBIT-5001"]
        desc = app.build_description(
            entry, owner, "owner-ORBIT", "ORBIT", owners
        )
        assert desc == "ORBIT-5001 – Task 001"

    def test_description_subproject_empty(self, sample_json_data):
        """Description with empty note on sub-project should still show project name."""
        owners = app.build_owner_map(sample_json_data)
        entry = app.ProcessedEntry(
            date="01.03.2024",
            date_iso="2024-03-01",
            owner_id="owner-meeting",
            owner_name="Meeting",
            start_dt=datetime(2024, 3, 1, 11, 0, tzinfo=BERLIN),
            stop_dt=datetime(2024, 3, 1, 12, 0, tzinfo=BERLIN),
            duration_h=1.0,
            note="",
            is_pause=False,
        )
        owner = owners["owner-meeting"]
        desc = app.build_description(
            entry, owner, "owner-ORBIT", "ORBIT", owners
        )
        assert desc == "Meeting"


class TestOwnerHierarchy:
    def test_descendants(self, sample_json_data):
        """Test descendants function."""
        owners = app.build_owner_map(sample_json_data)
        desc = app.descendants("owner-ORBIT", owners)
        # Should include ORBIT and its sub-projects
        assert "owner-ORBIT" in desc
        assert "owner-ORBIT-5001" in desc
        assert "owner-meeting" in desc
        assert "owner-pause" not in desc

    def test_ancestors(self, sample_json_data):
        """Test ancestors function."""
        owners = app.build_owner_map(sample_json_data)
        anc = app.ancestors("owner-ORBIT-5001", owners)
        assert "owner-ORBIT-5001" in anc
        assert "owner-ORBIT" in anc
        assert "owner-pause" not in anc


class TestEndToEnd:
    def test_filter_and_round(self, sample_json_data):
        """Test end-to-end filtering and rounding."""
        owners = app.build_owner_map(sample_json_data)
        entries = app.build_entries(sample_json_data)
        pause_owner_ids = app.descendants("owner-pause", owners)

        # Filter for March 2024
        processed = app.filter_and_round(entries, owners, 2024, 3, pause_owner_ids)
        # Should include both work and pause entries
        assert len(processed) > 0
        # Count pause vs work entries
        pause_entries = [e for e in processed if e.is_pause]
        work_entries = [e for e in processed if not e.is_pause]
        assert len(pause_entries) > 0
        assert len(work_entries) > 0

    def test_csv_output(self, sample_json_data, temp_output_dir):
        """Test CSV output generation."""
        # Save JSON to temp file
        json_file = temp_output_dir / "test.json"
        json_file.write_text(json.dumps(sample_json_data), encoding="utf-8")

        # Load and process data
        owners = app.build_owner_map(sample_json_data)
        entries = app.build_entries(sample_json_data)
        pause_owner_ids = app.descendants("owner-pause", owners)
        processed = app.filter_and_round(entries, owners, 2024, 3, pause_owner_ids)

        # Write CSV 1
        primary_ids = app.descendants("owner-ORBIT", owners)
        csv1_path = temp_output_dir / "test_detail.csv"
        app.write_primary_csv(
            processed, owners, primary_ids, "owner-ORBIT", "ORBIT", csv1_path
        )

        # Write CSV 2
        csv2_path = temp_output_dir / "test_summary.csv"
        app.write_daily_csv(processed, owners, "owner-ORBIT", csv2_path)

        # Verify files exist and have content
        assert csv1_path.exists()
        assert csv2_path.exists()

        csv1_content = csv1_path.read_text(encoding="utf-8-sig")
        csv2_content = csv2_path.read_text(encoding="utf-8-sig")

        # Check headers
        assert "Datum" in csv1_content
        assert "Uhrzeit von" in csv1_content
        assert "Dauer" in csv1_content
        assert "Anfang" in csv2_content
        assert "Ende" in csv2_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


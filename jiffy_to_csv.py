"""
jiffy_to_csv.py
===============
Exports a single month from a Jiffy time-tracking JSON backup into two CSV files.

Usage
-----
    python jiffy_to_csv.py --month YYYY-MM [--input FILE] [--output-dir DIR] [--primary-project NAME]

Arguments
---------
--month            Required. Target month in YYYY-MM format.
--input            Optional. Path to the Jiffy JSON file.
                   Defaults to the first *.json file found in the current directory.
--output-dir       Optional. Directory where the CSVs are written.
                   Defaults to the current directory.
--primary-project  Optional. Name of the primary project.
                   CSV 1 uses this project tree. CSV 2 shows only its top-level sub-projects.

Output
------
CSV 1  <month>_<primary>_detail.csv
    One row per time entry belonging to the primary project tree.
    Columns: Datum; Uhrzeit von; Uhrzeit bis; Pause; Dauer; Beschreibung
    If description is empty, the primary project name is used instead.

CSV 2  <month>_daily_summary.csv
    One row per calendar day that has tracked entries (all projects).
    Columns: Datum; Anfang; Ende; <ProjectName> ... (one column per top-level sub-project only)

Rules applied
-------------
- Only entries with status ACTIVE are included (DELETED entries are skipped).
- Start time is floored to the nearest 15-minute boundary.
- Stop  time is ceiled  to the nearest 15-minute boundary.
- Duration is recomputed from the rounded times.
- Break (Pause) rules per day across ALL projects:
      net work <=  6 h  →  no mandatory break
      net work  >  6 h, <= 9 h  →  break must be at least 0:30 h
      net work  >  9 h          →  break must be at least 0:45 h
      net work > 10 h            →  break is increased until net = exactly 10:00 h
  Explicit "Pause" owner entries are recognised and counted.
  If the mandatory break cannot be satisfied by existing gaps, a synthetic break
  is inserted (preferably near 12:00) and logged to stderr.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
QUARTER_HOUR_MS = 15 * 60 * 1_000  # 15 minutes in milliseconds
BERLIN = ZoneInfo("Europe/Berlin")

# Break thresholds (in hours)
BREAK_THRESHOLD_NONE = 6.0       # <= 6 h  → no break required
BREAK_THRESHOLD_SHORT = 9.0      # <= 9 h  → 30 min break required
BREAK_MIN_SHORT = 0.5            # 30 minutes
BREAK_MIN_LONG = 0.75            # 45 minutes
MAX_NET_HOURS = 10.0             # Hard cap: net work must not exceed 10 h
DEFAULT_PRIMARY_PROJECT = "".join(["A", "D", "E", "B", "A", "R"])


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class Owner(NamedTuple):
    id: str
    name: str
    parent_id: str | None  # None for top-level owners
    status: str            # "ACTIVE" or "ARCHIVED"


class Entry(NamedTuple):
    id: str
    owner_id: str
    start_ms: int          # original start timestamp in ms
    stop_ms: int           # original stop  timestamp in ms
    note: str
    status: str            # "ACTIVE" | "DELETED"


# ---------------------------------------------------------------------------
# Step 1 – JSON parsing helpers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    """Load and return the Jiffy backup JSON."""
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def build_owner_map(data: dict) -> dict[str, Owner]:
    """Return {owner_id: Owner} from the time_owners array."""
    owners: dict[str, Owner] = {}
    for raw in data.get("time_owners", []):
        owners[raw["id"]] = Owner(
            id=raw["id"],
            name=raw.get("name", ""),
            parent_id=raw.get("parent_id"),
            status=raw.get("status", "ACTIVE"),
        )
    return owners


def build_entries(data: dict) -> list[Entry]:
    """Return all time entries from the backup."""
    entries: list[Entry] = []
    for raw in data.get("time_entries", []):
        entries.append(Entry(
            id=raw["id"],
            owner_id=raw.get("owner_id", ""),
            start_ms=raw.get("start_time", 0),
            stop_ms=raw.get("stop_time", 0),
            note=raw.get("note", ""),
            status=raw.get("status", "ACTIVE"),
        ))
    return entries


# ---------------------------------------------------------------------------
# Step 2 – Owner hierarchy helpers
# ---------------------------------------------------------------------------

def ancestors(owner_id: str, owners: dict[str, Owner]) -> list[str]:
    """Return [owner_id, parent_id, grandparent_id, ...] walking up the tree."""
    chain: list[str] = []
    current = owner_id
    seen: set[str] = set()
    while current and current not in seen:
        chain.append(current)
        seen.add(current)
        current = owners[current].parent_id if current in owners else None
    return chain


def descendants(owner_id: str, owners: dict[str, Owner]) -> set[str]:
    """Return all owner IDs that are descendants (children, grandchildren…) of owner_id,
    including owner_id itself."""
    result: set[str] = {owner_id}
    # one-pass expansion; Jiffy trees are shallow so two passes are enough
    changed = True
    while changed:
        changed = False
        for oid, owner in owners.items():
            if oid not in result and owner.parent_id in result:
                result.add(oid)
                changed = True
    return result


def find_owner_by_name(name: str, owners: dict[str, Owner]) -> Owner | None:
    """Return the first owner whose name matches exactly (case-sensitive)."""
    for owner in owners.values():
        if owner.name == name:
            return owner
    return None


# ---------------------------------------------------------------------------
# Step 3 – Time utilities
# ---------------------------------------------------------------------------

def ms_to_local_dt(ms: int) -> datetime:
    """Convert a Unix-millisecond timestamp to a timezone-aware datetime in Berlin."""
    utc_dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return utc_dt.astimezone(BERLIN)


def floor_15(dt: datetime) -> datetime:
    """Floor a datetime to the nearest 15-minute boundary."""
    minutes = (dt.minute // 15) * 15
    return dt.replace(minute=minutes, second=0, microsecond=0)


def ceil_15(dt: datetime) -> datetime:
    """Ceil a datetime to the nearest 15-minute boundary."""
    if dt.second == 0 and dt.microsecond == 0 and dt.minute % 15 == 0:
        return dt.replace(second=0, microsecond=0)
    minutes = math.ceil(dt.minute / 15) * 15
    if minutes >= 60:
        return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return dt.replace(minute=minutes, second=0, microsecond=0)


def duration_hours(start: datetime, stop: datetime) -> float:
    """Return duration in decimal hours (rounded to 4 decimal places)."""
    return round((stop - start).total_seconds() / 3600.0, 4)


def format_time(dt: datetime) -> str:
    """Return HH:MM string."""
    return dt.strftime("%H:%M")


def format_date(dt: datetime) -> str:
    """Return DD.MM.YYYY string."""
    return dt.strftime("%d.%m.%Y")


def hours_to_hhmm(h: float) -> str:
    """Convert decimal hours to HH:MM string."""
    total_minutes = round(h * 60)
    return f"{total_minutes // 60}:{total_minutes % 60:02d}"


def format_hours_comma(h: float) -> str:
    """Format hours as German decimal string (comma separator, 2 decimal places)."""
    return f"{h:.2f}".replace(".", ",")


# ---------------------------------------------------------------------------
# Step 4 – Month filter
# ---------------------------------------------------------------------------

class ProcessedEntry(NamedTuple):
    date: str               # DD.MM.YYYY
    date_iso: str           # YYYY-MM-DD  (for sorting/grouping)
    owner_id: str
    owner_name: str
    start_dt: datetime      # rounded
    stop_dt: datetime       # rounded
    duration_h: float       # from rounded times
    note: str
    is_pause: bool          # True when this entry belongs to a "Pause" owner


def filter_and_round(
    entries: list[Entry],
    owners: dict[str, Owner],
    year: int,
    month: int,
    pause_owner_ids: set[str],
) -> list[ProcessedEntry]:
    """
    Keep only ACTIVE entries whose local date falls within (year, month),
    round start/stop times and compute duration.
    """
    result: list[ProcessedEntry] = []
    for e in entries:
        if e.status != "ACTIVE":
            continue
        if e.stop_ms <= e.start_ms:
            continue  # degenerate entry

        start_local = ms_to_local_dt(e.start_ms)
        stop_local  = ms_to_local_dt(e.stop_ms)

        # Use the date of the start timestamp for month assignment
        if start_local.year != year or start_local.month != month:
            continue

        start_r = floor_15(start_local)
        stop_r  = ceil_15(stop_local)

        if stop_r <= start_r:
            # After rounding the entry collapsed to zero – skip it
            continue

        dur = duration_hours(start_r, stop_r)
        owner = owners.get(e.owner_id)
        owner_name = owner.name if owner else e.owner_id

        result.append(ProcessedEntry(
            date=format_date(start_r),
            date_iso=start_r.strftime("%Y-%m-%d"),
            owner_id=e.owner_id,
            owner_name=owner_name,
            start_dt=start_r,
            stop_dt=stop_r,
            duration_h=dur,
            note=e.note or "",
            is_pause=(e.owner_id in pause_owner_ids),
        ))

    # Sort chronologically, then enforce non-overlapping ranges per day.
    result.sort(key=lambda e: (e.date_iso, e.start_dt, e.stop_dt))

    normalized: list[ProcessedEntry] = []
    by_day = group_by_day(result)
    for date_iso in sorted(by_day):
        prev_stop: datetime | None = None
        for entry in by_day[date_iso]:
            start_n = entry.start_dt
            stop_n = entry.stop_dt

            if prev_stop is not None and start_n < prev_stop:
                log.error(
                    "Overlap after rounding on %s: %s-%s overlaps previous entry ending at %s. "
                    "Adjusting start to %s.",
                    date_iso,
                    format_time(start_n),
                    format_time(stop_n),
                    format_time(prev_stop),
                    format_time(prev_stop),
                )
                start_n = prev_stop

            if stop_n <= start_n:
                log.error(
                    "Entry collapsed after overlap fix on %s: start=%s stop=%s. Skipping entry.",
                    date_iso,
                    format_time(start_n),
                    format_time(stop_n),
                )
                continue

            if start_n != entry.start_dt:
                entry = ProcessedEntry(
                    date=format_date(start_n),
                    date_iso=start_n.strftime("%Y-%m-%d"),
                    owner_id=entry.owner_id,
                    owner_name=entry.owner_name,
                    start_dt=start_n,
                    stop_dt=stop_n,
                    duration_h=duration_hours(start_n, stop_n),
                    note=entry.note,
                    is_pause=entry.is_pause,
                )

            normalized.append(entry)
            prev_stop = entry.stop_dt

    return normalized


# ---------------------------------------------------------------------------
# Step 5 – Break / Pause logic
# ---------------------------------------------------------------------------

def required_break(net_hours: float) -> float:
    """Return the minimum required break in hours for a given net working time."""
    if net_hours <= BREAK_THRESHOLD_NONE:
        return 0.0
    if net_hours <= BREAK_THRESHOLD_SHORT:
        return BREAK_MIN_SHORT
    return BREAK_MIN_LONG


def compute_day_breaks(
    day_entries: list[ProcessedEntry],
    date_iso: str,
) -> tuple[float, float]:
    """
    Given all entries for one day (sorted by start time), compute and enforce breaks.

    Returns:
        (total_break_hours_including_synthetic, synthetic_break_hours)

    Logic:
      1. Sum explicit pause-owner entries.
      2. Sum net working time (non-pause entries).
      3. Check gaps between consecutive entries as implicit breaks.
      4. If total breaks < required → synthesise a break near 12:00 and log it.
      5. If net > 10 h → add extra synthetic break and log it.
    """
    if not day_entries:
        return 0.0, 0.0

    # Separate pause entries from work entries
    work_entries = [e for e in day_entries if not e.is_pause]
    pause_entries = [e for e in day_entries if e.is_pause]

    # Total explicit pause time (Pause-owner entries)
    explicit_pause_h = sum(e.duration_h for e in pause_entries)

    # Net work time (work entries only, before any gap analysis)
    net_work_h = sum(e.duration_h for e in work_entries)

    # Gaps between consecutive work entries (implicit breaks)
    implicit_gap_h = 0.0
    for i in range(1, len(work_entries)):
        prev_stop  = work_entries[i - 1].stop_dt
        curr_start = work_entries[i].start_dt
        if curr_start > prev_stop:
            implicit_gap_h += duration_hours(prev_stop, curr_start)

    total_break_h = explicit_pause_h + implicit_gap_h

    req = required_break(net_work_h)
    missing_break_h = max(0.0, req - total_break_h)
    over_cap = False

    if net_work_h > MAX_NET_HOURS:
        excess = net_work_h - MAX_NET_HOURS
        missing_break_h = max(missing_break_h, excess)
        over_cap = True
        log.warning(
            "Day %s: net work %.2f h exceeds 10 h cap — adding %.2f h synthetic break to reach ≤10 h.",
            date_iso, net_work_h, missing_break_h,
        )

    if missing_break_h > 0 and work_entries and not over_cap:
        log.info(
            "Day %s: adding %.2f h synthetic break (mandatory break not fully covered).",
            date_iso, missing_break_h,
        )

    return total_break_h + missing_break_h, missing_break_h


def group_by_day(entries: list[ProcessedEntry]) -> dict[str, list[ProcessedEntry]]:
    """Return {date_iso: [entries]} sorted chronologically within each day."""
    groups: dict[str, list[ProcessedEntry]] = defaultdict(list)
    for e in entries:
        groups[e.date_iso].append(e)
    # Sort entries within each day by start time
    for date_iso in groups:
        groups[date_iso].sort(key=lambda e: e.start_dt)
    return dict(sorted(groups.items()))


# ---------------------------------------------------------------------------
# Step 6 – Description builder
# ---------------------------------------------------------------------------

def build_description(
    entry: ProcessedEntry,
    owner: Owner | None,
    primary_id: str,
    primary_name: str,
    owners: dict[str, Owner],
) -> str:
    """
    Build the description string for CSV 1.

    If the owner IS the primary project root, use note; if note is empty, use primary project name.
    If the owner is a sub-project, return sub-project name + optional note.
    """
    if owner is None:
        return entry.note or primary_name

    if owner.id == primary_id:
        return entry.note or primary_name

    parts = [owner.name]
    if entry.note:
        parts.append(entry.note)
    return " – ".join(parts)


# ---------------------------------------------------------------------------
# Step 7 – CSV 1: Primary project detail
# ---------------------------------------------------------------------------

def write_primary_csv(
    all_entries: list[ProcessedEntry],
    owners: dict[str, Owner],
    primary_ids: set[str],
    primary_root_id: str,
    primary_name: str,
    out_path: Path,
) -> None:
    """
    Write the primary project detail CSV.

        Columns: Datum; Uhrzeit von; Uhrzeit bis; Pause; Dauer; Beschreibung
        - One row per time entry that belongs to the primary project tree.
        - Gaps between entries are considered for day-break rules, but are NOT written
            to the CSV pause column.
        - The Pause column contains only synthetic pause that had to be added because
            mandatory pause requirements were not fully covered by real gaps/pause entries.
    """
    # Filter to primary project scope
    primary_entries = [e for e in all_entries if e.owner_id in primary_ids]

    # Group ALL entries by day for day-break computation (across all projects)
    all_by_day = group_by_day(all_entries)

    with out_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["Datum", "Uhrzeit von", "Uhrzeit bis", "Pause", "Dauer", "Beschreibung"])

        primary_by_day = group_by_day(primary_entries)
        for date_iso in sorted(primary_by_day):
            day_primary = primary_by_day[date_iso]
            all_day_entries = all_by_day.get(date_iso, [])
            _, synthetic_break_h = compute_day_breaks(all_day_entries, date_iso)

            # Default: no pause shown in CSV rows (gaps are intentionally not listed).
            pause_per_row_h = [0.0] * len(day_primary)

            if synthetic_break_h > 0 and day_primary:
                # Put synthetic pause near 12:00 on an entry that can absorb it.
                noon = day_primary[0].start_dt.replace(hour=12, minute=0, second=0, microsecond=0)
                fitting = [
                    (i, e) for i, e in enumerate(day_primary)
                    if e.duration_h >= synthetic_break_h
                ]

                if fitting:
                    containing_noon = [
                        (i, e) for i, e in fitting
                        if e.start_dt <= noon <= e.stop_dt
                    ]

                    candidates = containing_noon if containing_noon else fitting

                    target_idx, _ = min(
                        candidates,
                        key=lambda ie: abs(
                            ((ie[1].start_dt + (ie[1].stop_dt - ie[1].start_dt) / 2) - noon).total_seconds()
                        ),
                    )
                else:
                    # Fallback: nearest entry to noon, even if too short.
                    target_idx, _ = min(
                        enumerate(day_primary),
                        key=lambda ie: abs(
                            ((ie[1].start_dt + (ie[1].stop_dt - ie[1].start_dt) / 2) - noon).total_seconds()
                        ),
                    )

                pause_per_row_h[target_idx] = synthetic_break_h

                if day_primary[target_idx].duration_h < synthetic_break_h:
                    log.error(
                        "Day %s: synthetic pause %.2f h exceeds target entry duration %.2f h.",
                        date_iso,
                        synthetic_break_h,
                        day_primary[target_idx].duration_h,
                    )

            for idx, entry in enumerate(day_primary):
                pause_h = pause_per_row_h[idx]
                pause_str = hours_to_hhmm(pause_h)

                owner = owners.get(entry.owner_id)
                description = build_description(entry, owner, primary_root_id, primary_name, owners)

                writer.writerow([
                    entry.date,
                    format_time(entry.start_dt),
                    format_time(entry.stop_dt),
                    pause_str,
                    format_hours_comma(entry.duration_h),
                    description,
                ])

    log.info("CSV 1 written → %s  (%d rows)", out_path, len(primary_entries))


# ---------------------------------------------------------------------------
# Step 8 – CSV 2: Daily summary (root-level projects only)
# ---------------------------------------------------------------------------

def write_daily_csv(
    all_entries: list[ProcessedEntry],
    owners: dict[str, Owner],
    primary_root_id: str,
    out_path: Path,
) -> None:
    """
    Write the daily summary CSV.

    Columns: Datum; Anfang; Ende; <project_name>...
    - One row per calendar day.
    - Project columns contain summed decimal hours (comma notation) for root-level
      projects (owners with parent_id == None).
    - Sub-projects are NOT shown as separate columns; their hours are rolled up
      into their root project column.
    - Columns are sorted alphabetically by project name.
    - Day start = minimum rounded start time among all entries of the day.
    - Day end   = maximum rounded stop  time among all entries of the day.
    """
    # Keep signature backward-compatible; CSV 2 now aggregates by root owners globally.
    _ = primary_root_id

    def top_level_owner(owner_id: str) -> Owner | None:
        """Return the owner that sits directly under the global root.

        Example: root=Company, child=primary project, subchild=sub-project -> returns primary project.
        """
        current_id = owner_id
        prev_id: str | None = None
        seen: set[str] = set()

        while current_id and current_id not in seen and current_id in owners:
            seen.add(current_id)
            current = owners[current_id]
            if current.parent_id is None:
                # current is global root; return previous node below root if present.
                return owners[prev_id] if prev_id and prev_id in owners else current
            prev_id = current_id
            current_id = current.parent_id

        return None

    work_entries_all = [e for e in all_entries if not e.is_pause]
    if not work_entries_all:
        log.info("No work entries found. No CSV 2 written.")
        return

    # Collect root project names that actually appear in this export month.
    project_names: set[str] = set()
    for e in work_entries_all:
        top = top_level_owner(e.owner_id)
        project_names.add(top.name if top else e.owner_name)

    sorted_projects = sorted(project_names)

    day_groups = group_by_day(all_entries)  # use ALL entries for grouping

    with out_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["Datum", "Anfang", "Ende"] + sorted_projects)

        for date_iso in sorted(day_groups):
            day_entries = day_groups[date_iso]
            work_entries = [e for e in day_entries if not e.is_pause]

            if not work_entries:
                continue

            day_start = min(e.start_dt for e in work_entries)
            day_end   = max(e.stop_dt  for e in work_entries)

            # Sum hours per project
            project_hours: dict[str, float] = defaultdict(float)
            for e in work_entries:
                top = top_level_owner(e.owner_id)
                top_name = top.name if top else e.owner_name
                project_hours[top_name] += e.duration_h

            row = [
                day_entries[0].date,
                format_time(day_start),
                format_time(day_end),
            ]
            for proj in sorted_projects:
                h = project_hours.get(proj, 0.0)
                row.append(format_hours_comma(h) if h > 0.0 else "")

            writer.writerow(row)

    log.info(
        "CSV 2 written → %s  (%d days)",
        out_path,
        len([d for d in day_groups if any(e for e in day_groups[d] if not e.is_pause)]),
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def find_default_json(cwd: Path) -> Path | None:
    """Return the first *.json file in cwd, or None."""
    candidates = sorted(cwd.glob("*.json"))
    return candidates[0] if candidates else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a Jiffy time-tracking month to two CSV files.",
    )
    parser.add_argument(
        "--month",
        required=True,
        metavar="YYYY-MM",
        help="Target month to export (e.g. 2024-03).",
    )
    parser.add_argument(
        "--input",
        metavar="FILE",
        help="Path to the Jiffy JSON backup. Defaults to the first *.json in CWD.",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        default=".",
        help="Directory for the output CSV files. Defaults to current directory.",
    )
    parser.add_argument(
        "--primary-project",
        metavar="NAME",
        default=DEFAULT_PRIMARY_PROJECT,
        help="Name of the primary project. CSV 1 uses this project tree.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # --- Validate month format ---
    try:
        month_dt = datetime.strptime(args.month, "%Y-%m")
    except ValueError:
        log.error("Invalid --month format. Expected YYYY-MM, got: %s", args.month)
        sys.exit(1)
    year, month = month_dt.year, month_dt.month

    # --- Resolve input file ---
    cwd = Path.cwd()
    if args.input:
        input_path = Path(args.input)
    else:
        input_path = find_default_json(cwd)

    if input_path is None or not input_path.is_file():
        log.error(
            "No Jiffy JSON file found. Provide --input or place a *.json file in the current directory."
        )
        sys.exit(1)

    # --- Resolve output directory ---
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Load data ---
    log.info("Loading %s …", input_path)
    data = load_json(input_path)
    owners = build_owner_map(data)
    entries = build_entries(data)
    log.info("Loaded %d owners, %d entries.", len(owners), len(entries))

    # --- Locate special owners ---
    primary_owner = find_owner_by_name(args.primary_project, owners)
    if primary_owner is None:
        log.error("Could not find an owner named '%s' in the JSON.", args.primary_project)
        sys.exit(1)

    pause_owner = find_owner_by_name("Pause", owners)
    pause_owner_ids: set[str] = (
        descendants(pause_owner.id, owners) if pause_owner else set()
    )
    primary_ids: set[str] = descendants(primary_owner.id, owners)

    log.info(
        "%s tree: %d owners.  Pause owner(s): %d.",
        args.primary_project,
        len(primary_ids),
        len(pause_owner_ids),
    )

    # --- Filter & round entries ---
    processed = filter_and_round(entries, owners, year, month, pause_owner_ids)
    log.info(
        "Month %s: %d active entries after filtering.", args.month, len(processed)
    )

    if not processed:
        log.warning("No entries found for %s. No CSV files will be written.", args.month)
        sys.exit(0)

    # --- Output paths ---
    csv1_path = out_dir / f"{args.month}_{args.primary_project.lower()}_detail.csv"
    csv2_path = out_dir / f"{args.month}_daily_summary.csv"

    # --- Write CSV 1 ---
    write_primary_csv(
        processed, owners, primary_ids, primary_owner.id, args.primary_project, csv1_path
    )

    # --- Write CSV 2 ---
    write_daily_csv(processed, owners, primary_owner.id, csv2_path)

    log.info("Done.")


if __name__ == "__main__":
    main()

# jiffy-to-csv

Export monthly time-tracking data from a Jiffy JSON backup into two structured CSV files with automatic break calculation and time rounding.

## Features

- **Time rounding**: All start times are floored to the nearest 15-minute boundary; stop times are ceiled to the nearest 15-minute boundary.
- **Automatic break calculation**: Mandatory breaks are computed based on net working hours, with synthetic breaks inserted if gaps are insufficient.
- **Dual CSV output**:
  - **CSV 1** (detail): One row per time entry in the primary project tree, with break tracking and descriptions.
  - **CSV 2** (daily summary): One row per day, aggregating hours by top-level sub-projects.
- **Parameterizable project**: Specify any project as the "primary" project (default: ORBIT); CSV 1 focuses on this project tree.
- **Pause tracking**: Automatic detection of pause entries and integration into daily break calculations.
- **UTF-8-sig encoding**: CSV files are properly encoded for Excel compatibility.

## Installation

Requires Python 3.9 or later (for `zoneinfo` timezone support). No external dependencies required.

### Setup

Clone the repository and navigate to the directory:

```bash
cd jiffy-to-csv
python jiffy_to_csv.py --help
```

## Usage

### Basic Example

Export March 2024 data:

```bash
python jiffy_to_csv.py --month 2024-03
```

Assumes a `*.json` file exists in the current directory. The script will locate it automatically.

### All Parameters

```bash
python jiffy_to_csv.py --month 2024-03 --input backup.json --output-dir ./exports --primary-project ORBIT
```

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--month` | Yes | Target month in `YYYY-MM` format (e.g., `2024-03`). |
| `--input` | No | Path to the Jiffy JSON backup file. Defaults to the first `*.json` file found in the current directory. |
| `--output-dir` | No | Output directory for CSV files. Defaults to the current directory. |
| `--primary-project` | No | Name of the primary project (default: `ORBIT`). CSV 1 focuses on entries from this project tree; CSV 2 shows only its top-level sub-projects. |

### Examples

#### Example 1: Export March 2024 with defaults
```bash
python jiffy_to_csv.py --month 2024-03
```
**Output**: `2024-03_ORBIT_detail.csv` and `2024-03_daily_summary.csv` (in the current directory)

#### Example 2: Export with custom primary project
```bash
python jiffy_to_csv.py --month 2024-03 --primary-project "BA Allgemein"
```
**Output**: `2024-03_ba allgemein_detail.csv` and `2024-03_daily_summary.csv`

#### Example 3: Specify input and output paths
```bash
python jiffy_to_csv.py --month 2024-03 --input ./backups/jiffy.json --output-dir ./exports
```
**Output**: `./exports/2024-03_ORBIT_detail.csv` and `./exports/2024-03_daily_summary.csv`

## Output Formats

### CSV 1: Detail Export (Primary Project)

**Filename**: `<month>_<primary-project>_detail.csv`

**Columns**:
- `Datum`: Date in DD.MM.YYYY format
- `Uhrzeit von`: Start time in HH:MM format
- `Uhrzeit bis`: Stop time in HH:MM format
- `Pause`: Break duration in HH:MM format
- `Dauer`: Work duration in decimal hours (comma notation, e.g., `1,75`)
- `Beschreibung`: Description (project name and/or note)

**Pause Column Logic**:
- For the first entry of each day: total mandatory break computed for that day
- For subsequent entries: gap between the previous entry's stop time and this entry's start time
- Pause time is always formatted in HH:MM

**Description Fallback**:
- If the entry has a note, the description is the note (or `<project> – <note>` for sub-projects)
- If the entry has no note and belongs to the primary project root, the primary project name is used as a fallback
- If the entry has no note and belongs to a sub-project, the sub-project name is used

### CSV 2: Daily Summary

**Filename**: `<month>_daily_summary.csv`

**Columns**:
- `Datum`: Date in DD.MM.YYYY format
- `Anfang`: Earliest start time of the day (HH:MM)
- `Ende`: Latest stop time of the day (HH:MM)
- `<TopLevelProject1>`, `<TopLevelProject2>`, ...: Hours per top-level sub-project (comma notation)

**Notes**:
- Only entries from **top-level sub-projects** of the primary project are included (excluding further nested projects)
- Hours are summed per project per day
- Empty cells represent 0 hours
- Project columns are sorted alphabetically

## Break Rules

The script automatically calculates mandatory breaks based on German labor law (as of March 2024):

| Net Work Hours | Mandatory Break |
|---|---|
| ≤ 6 h | None |
| > 6 h and ≤ 9 h | ≥ 0:30 (30 minutes) |
| > 9 h | ≥ 0:45 (45 minutes) |
| > 10 h | Capped to 10 h net (break increased to reach exactly 10 h) |

### Synthetic Breaks

If the existing gaps between entries do not satisfy the mandatory break, a synthetic break is automatically inserted:
- Preferably near noon (12:00)
- Logged as an INFO or WARNING message
- Included in the "Pause" column of CSV 1

### Break Tracking

- **Pause entries**: The script recognizes entries belonging to a "Pause" owner and excludes them from work duration calculations.
- **Daily totals**: All gaps (including pause entries) are counted toward the mandatory break requirement.

## Example Workflow

### Step 1: Prepare the Jiffy backup
Export your Jiffy time-tracking data as a JSON file (usually named `jiffy-*.json`).

### Step 2: Run the export
```bash
cd ~/my-jiffy-exports
python ~/jiffy-to-csv/jiffy_to_csv.py --month 2024-03 --input ./jiffy-backup.json
```

### Step 3: Review the output
- **CSV 1** (`2024-03_ORBIT_detail.csv`): Import into Excel for detailed time entry review
- **CSV 2** (`2024-03_daily_summary.csv`): Use for daily time reporting or project hour summaries

## Time Rounding

All times are rounded to the nearest 15-minute boundary:

- **Start times**: Floored (rounded down) to the nearest 15-minute boundary
  - Example: 08:37 → 08:30
- **Stop times**: Ceiled (rounded up) to the nearest 15-minute boundary
  - Example: 08:37 → 08:45
- **Duration**: Recomputed from the rounded times

This ensures all entries align with common invoicing intervals (0.25 h increments).

## Testing

Run the unit tests:

```bash
pytest test_jiffy_to_csv.py -v
```

The test suite includes:
- JSON parsing and owner hierarchy navigation
- Time rounding logic
- Description generation and fallbacks
- Break computation
- End-to-end CSV output

## Logging

The script logs informational and warning messages to `stderr`:

- **INFO**: Synthetic break insertion, file writing progress, processing summary
- **WARNING**: Days exceeding the 10-hour work cap

Example output:
```
INFO: Loading jiffy-backup.json …
INFO: Loaded 232 owners, 9176 entries.
INFO: ORBIT tree: 220 owners.  Pause owner(s): 1.
INFO: Month 2024-03: 114 active entries after filtering.
INFO: Day 2024-03-01: adding 0.75 h synthetic break (mandatory break not fully covered).
INFO: CSV 1 written → 2024-03_ORBIT_detail.csv  (104 rows)
INFO: CSV 2 written → 2024-03_daily_summary.csv  (18 days)
INFO: Done.
```

## Requirements

- **Python**: 3.9 or later
- **Standard Library**: No external dependencies

## License

This project is provided as-is for time-tracking export and analysis.

## Troubleshooting

### No Jiffy JSON file found
**Solution**: Place the JSON backup in the current directory or provide the `--input` parameter with the full path.

### "Could not find an owner named 'ORBIT'"
**Solution**: Check the JSON for the correct project name using `--primary-project`. Ensure the project exists in your Jiffy backup.

### CSV files are empty or have only headers
**Solution**: Verify that the JSON contains entries for the specified month. Check the log output for filtering details.

### Times are not rounded correctly
**Solution**: This is expected behavior. All times are rounded to 15-minute boundaries as per the rules above. Review your original Jiffy entries to confirm.

## Version History

- **1.0.0** (2024-03): Initial release with parameterizable primary project, top-level sub-project filtering, and description fallback logic.


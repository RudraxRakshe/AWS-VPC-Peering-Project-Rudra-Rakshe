# AWS VPC Peering - PDF Report Generator

Generates a PDF report (cover page, table of contents, objectives, architecture,
resource inventory, routing, security, test results, troubleshooting, lessons
learned, cleanup, numbered figures, page numbers) from `project_config.json`
using Python and ReportLab.

Student: Rudra Sanjay Rakshe | Project: AWS VPC Peering | Region: ap-southeast-2 (Sydney)

## Folder structure

```
report_generator/
├── generate_report.py      # the generator
├── project_config.json     # sample configuration (edit this)
├── requirements.txt
├── README.md
├── assets/
│   └── architecture.png    # design schematic (replace with your own if you like)
├── screenshots/            # YOU create this and put your screenshots here
└── output/                 # created automatically; PDFs are written here
```

## Install and run (Windows)

You need Python from python.org (tick "Add Python to PATH"). The code avoids new syntax and should work on 3.8+, but it was only tested on Python 3.13.
Open PowerShell in the `report_generator` folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python generate_report.py
```

If PowerShell blocks the activate script, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use
`.venv\Scripts\activate.bat` in Command Prompt.

The PDF is written to `output\VPC_Peering_Report.pdf`.
macOS/Linux: use `python3 -m venv .venv`, `source .venv/bin/activate`, then the same commands.

### Command-line options

| Option | Meaning |
|---|---|
| `--config FILE` | Use a different JSON file (default: `project_config.json`) |
| `--output FILE` | PDF path (default: `output/VPC_Peering_Report.pdf`) |
| `--force` | Overwrite the output file. Without it, an existing PDF is never overwritten; a numbered copy (`..._1.pdf`) is written instead. |
| `--validate-only` | Check the config and image files, print warnings, create no PDF |
| `--debug` | Show full error details |

## Adding screenshots

1. Create a `screenshots` folder next to `generate_report.py` and copy your PNG or JPG files into it.
2. In `project_config.json`, add an entry to the `"screenshots"` list:

   ```json
   {
     "id": "ping-app-a-to-b",
     "path": "screenshots/ping_app_a_to_b.png",
     "caption": "Ping from App-A to App-B",
     "section": "tests"
   }
   ```

   - `id` - unique name used to refer to the image.
   - `path` - relative to the JSON file, or a full path. In JSON use `/` (`C:/Users/me/pic.png`) or doubled backslashes (`C:\\Users\\me\\pic.png`). Single backslashes inside the JSON file are invalid JSON.
   - `caption` - shown as "Figure N: caption".
   - `section` - where it appears: `architecture`, `resources`, `routing`, `security`, `tests`, `troubleshooting`, `cleanup`, or `general` (goes to an "Additional Evidence" section).
3. Link it to a test or cleanup step by adding its id to that item's `"screenshots"` list:

   ```json
   { "id": "T3", "name": "App-A to App-B (over peering)", "status": "Passed",
     "screenshots": ["ping-app-a-to-b"], "observed_output": "4 packets transmitted, 4 received, 0% packet loss" }
   ```
4. Run `python generate_report.py --validate-only` to catch typos, then generate the PDF.

Figures are numbered automatically in the order they appear in the report.
Supported formats: PNG, JPG/JPEG.

## Evidence rules (nothing is invented)

- A **test** shows **Passed** only if `status` is `"Passed"` **and** at least one of its `screenshots` ids points to an image file that exists and opens. Otherwise it is shown as **Not Verified**, with a note when you declared "Passed" without evidence.
- `"Failed"` is reported as you wrote it. Anything else is **Not Verified**.
- A **cleanup step** shows **Deleted** only if `status` is `"Deleted"` and its evidence image exists. By default every step is Not Verified and the report states that no deletion is claimed.
- A missing/unreadable screenshot is replaced by a visible **NOT VERIFIED** placeholder box, and is listed in Appendix A (Evidence Register) and Appendix B (Data Completeness Notes).
- Route tables, security-group rules and resource IDs are printed exactly as you typed them. The generator cannot check them against your AWS account, and the report says so.
- Troubleshooting entries, lessons learned and objectives come only from your config. If the lists are empty the report says nothing was recorded. The "Reference Checklist" in the Troubleshooting section is generic guidance and is labelled that way.
- The sample config's security-group sources (for example `<YOUR-PUBLIC-IP>/32`), route tables and test commands are **typical example values** for this architecture. Edit them to match what you actually built.
- `assets/architecture.png` is a design schematic generated for this project and is captioned as "not evidence of deployment".

## Configuration reference (`project_config.json`)

| Key | Contents |
|---|---|
| `project` | `student`, `name`, `region` (required); `title`, `region_label`, `course`, `institution`, `report_date` (optional; defaults to today) |
| `summary`, `objectives`, `conclusion_notes` | Free text / list of strings |
| `architecture` | `description`, `diagram`, `diagram_caption`, lists `vpcs`, `subnets`, `instances` |
| `resources` | List of `category`, `name`, `resource_id`, `address`, `details` (empty ID prints "Not supplied") |
| `route_tables` | `name`, `vpc`, `associated_subnets`, `routes` (`destination`, `target`, `notes`) |
| `security_groups` | `name`, `vpc`, `attached_to`, `inbound`, `outbound`; optional `nacls` |
| `tests` | `id`, `name`, `source`, `destination`, `command`, `expected`, `status`, `observed_output`, `screenshots`, `notes` |
| `screenshots` | `id`, `path`, `caption`, `section` |
| `troubleshooting` | `issue`, `cause`, `resolution`, `screenshots` |
| `lessons_learned` | List of strings |
| `cleanup.steps` | `action`, `resource`, `status`, `screenshots` (if omitted, a default 7-step list is used, all Not Verified) |

Keys starting with `_` (such as `_readme`) are ignored.

## Troubleshooting the tool

- **"Invalid JSON ... line X"** - a missing/extra comma or quote, or single backslashes in a Windows path.
- **"Cannot write the PDF"** - the PDF is open in a viewer; close it or use `--output`.
- **Placeholder instead of an image** - check the path and extension; `--validate-only` lists the reason for each image.
- **Odd characters or black squares** - the report uses ReportLab's built-in Helvetica/Courier fonts, which cover Western characters only. Avoid emoji and non-Latin text in the config.

## What has and has not been tested

Tested (Linux, Python 3.x, ReportLab 5.0.1, Pillow 12.3): PDF generation from the sample config (12 pages, page numbers, TOC, placeholders, diagram); a scratch config with real, corrupt, missing and backslash-path images (Passed/Failed/Not Verified/Deleted logic behaved as described above); invalid JSON, missing required fields, duplicate IDs, missing config file, a UTF-8 BOM file, and the no-overwrite behaviour.

**Not tested:** running on Windows, `pip install -r requirements.txt` (no internet access in the test environment), ReportLab 4.x, or any real screenshots from your AWS environment. The sample PDF therefore shows every test and cleanup step as Not Verified, which is deliberate.

#!/usr/bin/env python3
"""
AWS VPC Peering - PDF Report Generator
======================================

Reads ``project_config.json`` and produces a professional PDF report with
ReportLab: cover page, table of contents, objectives, architecture, resource
inventory, routing, security, test results, troubleshooting, lessons learned,
cleanup documentation, numbered figures and page numbers.

EVIDENCE RULES (the generator never invents results)
----------------------------------------------------
* A test is shown as "Passed" only if the config says "Passed" AND at least
  one referenced screenshot file really exists and is readable.
  Otherwise it is shown as "Not Verified".
* A cleanup step is shown as "Deleted" only if the config says "Deleted"
  AND at least one referenced evidence screenshot exists.
  Otherwise it is shown as "Not Verified".
* Missing screenshots are replaced by a clearly marked placeholder box.
* Route, security-group and inventory data are reproduced exactly as supplied
  by the author; the generator cannot check them against AWS.

Usage
-----
    python generate_report.py
    python generate_report.py --config my_config.json --output report.pdf
    python generate_report.py --validate-only
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    HRFlowable,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "project_config.json"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
DEFAULT_OUTPUT_NAME = "VPC_Peering_Report.pdf"

PAGE_W, PAGE_H = A4
MARGIN = 2.0 * cm
CONTENT_W = PAGE_W - 2 * MARGIN
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}

NAVY = colors.HexColor("#1f3a5f")
LIGHT = colors.HexColor("#f2f5f9")
GRID_GREY = colors.HexColor("#b8c2cc")
TEXT_GREY = colors.HexColor("#555555")

PASSED = "Passed"
FAILED = "Failed"
NOT_VERIFIED = "Not Verified"
DELETED = "Deleted"

STATUS_COLORS = {
    PASSED: "#1b7f3b",
    DELETED: "#1b7f3b",
    FAILED: "#b3261e",
    NOT_VERIFIED: "#b26a00",
}

# Order in which evidence figures appear in the report (and are numbered).
SECTION_ORDER = [
    "architecture", "resources", "routing", "security",
    "tests", "troubleshooting", "cleanup", "general",
]

DEFAULT_CLEANUP_STEPS = [
    ("Terminate EC2 instances", "Bastion host, App-A, App-B"),
    ("Delete VPC peering connection", "VPC A <-> VPC B"),
    ("Delete custom route tables", "Route tables created for the project"),
    ("Detach and delete Internet Gateway", "Internet Gateway"),
    ("Delete subnets", "Public subnet A, Private subnet A, Private subnet B"),
    ("Delete VPCs", "VPC A, VPC B"),
    ("Release Elastic IP addresses (if any)", "Elastic IPs"),
]

TROUBLESHOOTING_REFERENCE = [
    "Peering request not accepted: the connection must be in the Active state "
    "before any traffic can flow.",
    "Missing route: BOTH VPC route tables need a route to the other VPC's CIDR "
    "block that targets the peering connection.",
    "Route table not associated with the intended subnet (the subnet silently "
    "uses the main route table).",
    "Security group does not allow the source CIDR block of the other VPC for "
    "the protocol being tested (for example ICMP for ping, TCP 22 for SSH).",
    "Network ACL rules blocking inbound or outbound traffic (remember ACLs are "
    "stateless).",
    "Overlapping CIDR blocks cannot be peered; peering is also not transitive.",
    "SSH key permission or wrong user name when connecting through the bastion host.",
]


class ConfigError(Exception):
    """Raised when the configuration file cannot be read or is invalid."""


class Markup(str):
    """A string that already contains safe ReportLab paragraph markup."""


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

def esc(value: Any) -> str:
    """Escape arbitrary text for ReportLab paragraphs; keep line breaks."""
    return escape(str(value)).replace("\n", "<br/>")


def val(value: Any, empty: str = "Not supplied") -> Markup:
    """Escaped value, or a grey italic placeholder when empty."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return Markup('<font color="#777777"><i>%s</i></font>' % escape(empty))
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
        if not value:
            return val("", empty)
    return Markup(esc(value))


def status_markup(status: str) -> Markup:
    color = STATUS_COLORS.get(status, "#444444")
    return Markup('<font color="%s"><b>%s</b></font>' % (color, escape(status)))


def normalize_test_status(raw: Any) -> str:
    key = str(raw or "").strip().lower()
    if key in ("passed", "pass", "success", "successful"):
        return PASSED
    if key in ("failed", "fail", "failure"):
        return FAILED
    return NOT_VERIFIED


def normalize_cleanup_status(raw: Any) -> str:
    key = str(raw or "").strip().lower()
    if key in ("deleted", "done", "completed", "complete", "removed"):
        return DELETED
    return NOT_VERIFIED


# --------------------------------------------------------------------------
# Configuration loading and validation
# --------------------------------------------------------------------------

def load_config(path: Path) -> Dict[str, Any]:
    """Load the JSON configuration file with friendly error messages."""
    if not path.is_file():
        raise ConfigError("Configuration file not found: %s" % path)
    try:
        with path.open("r", encoding="utf-8-sig") as handle:  # utf-8-sig: Notepad BOM
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "Invalid JSON in %s (line %d, column %d): %s. "
            "Tip: Windows paths need forward slashes or doubled backslashes."
            % (path.name, exc.lineno, exc.colno, exc.msg)
        )
    except OSError as exc:
        raise ConfigError("Cannot read %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ConfigError("The top level of the configuration must be a JSON object.")
    return data


def _check_list(parent: Dict[str, Any], key: str, item_type: type,
                where: str, errors: List[str],
                required: Tuple[str, ...] = ()) -> None:
    """Validate that parent[key] (if present) is a list of item_type."""
    if key not in parent:
        return
    items = parent[key]
    if not isinstance(items, list):
        errors.append("%s%s must be a list." % (where, key))
        return
    for idx, item in enumerate(items, start=1):
        label = "%s%s[%d]" % (where, key, idx)
        if not isinstance(item, item_type):
            errors.append("%s must be a %s." % (label, "text string" if item_type is str else "JSON object"))
            continue
        if item_type is dict:
            for req in required:
                if not str(item.get(req, "")).strip():
                    errors.append("%s is missing required field '%s'." % (label, req))


def validate_config(cfg: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings). Errors stop the build; warnings do not."""
    errors: List[str] = []
    warnings: List[str] = []

    project = cfg.get("project")
    if not isinstance(project, dict):
        errors.append("'project' section is missing or not an object.")
        project = {}
    for req in ("student", "name", "region"):
        if not str(project.get(req, "")).strip():
            errors.append("project.%s is required." % req)

    _check_list(cfg, "objectives", str, "", errors)
    _check_list(cfg, "lessons_learned", str, "", errors)
    _check_list(cfg, "resources", dict, "", errors, ("name",))
    _check_list(cfg, "route_tables", dict, "", errors, ("name",))
    _check_list(cfg, "security_groups", dict, "", errors, ("name",))
    _check_list(cfg, "nacls", dict, "", errors, ("name",))
    _check_list(cfg, "tests", dict, "", errors, ("id", "name"))
    _check_list(cfg, "screenshots", dict, "", errors, ("id",))
    _check_list(cfg, "troubleshooting", dict, "", errors, ("issue",))

    arch = cfg.get("architecture", {})
    if not isinstance(arch, dict):
        errors.append("'architecture' must be an object.")
    else:
        for key in ("vpcs", "subnets", "instances"):
            _check_list(arch, key, dict, "architecture.", errors, ("name",))

    cleanup = cfg.get("cleanup", {})
    if not isinstance(cleanup, dict):
        errors.append("'cleanup' must be an object with a 'steps' list.")
    else:
        _check_list(cleanup, "steps", dict, "cleanup.", errors, ("action",))

    for rt in _dict_items(cfg.get("route_tables")):
        _check_list(rt, "routes", dict, "route table '%s' " % rt.get("name"), errors,
                    ("destination", "target"))
    for sg in _dict_items(cfg.get("security_groups")):
        for key in ("inbound", "outbound"):
            _check_list(sg, key, dict, "security group '%s' " % sg.get("name"), errors)

    # Unique IDs.
    for key in ("screenshots", "tests"):
        seen = set()
        for item in _dict_items(cfg.get(key)):
            ident = str(item.get("id", ""))
            if ident in seen:
                errors.append("Duplicate %s id: '%s'." % (key, ident))
            seen.add(ident)

    if errors:
        return errors, warnings

    # ---- Warnings (non-fatal) ----
    shot_ids = {str(s.get("id")) for s in _dict_items(cfg.get("screenshots"))}
    for test in _dict_items(cfg.get("tests")):
        raw = str(test.get("status", "")).strip()
        if raw and normalize_test_status(raw) == NOT_VERIFIED and raw.lower() != "not verified":
            warnings.append("Test %s has unrecognised status '%s'; treated as Not Verified." % (test["id"], raw))
        refs = test.get("screenshots", [])
        if not isinstance(refs, list):
            warnings.append("Test %s: 'screenshots' should be a list of screenshot ids." % test["id"])
            continue
        for ref in refs:
            if str(ref) not in shot_ids:
                warnings.append("Test %s references unknown screenshot id '%s'." % (test["id"], ref))
    for step in _dict_items((cfg.get("cleanup") or {}).get("steps")):
        for ref in step.get("screenshots", []) or []:
            if str(ref) not in shot_ids:
                warnings.append("Cleanup step '%s' references unknown screenshot id '%s'." % (step["action"], ref))
    for shot in _dict_items(cfg.get("screenshots")):
        section = str(shot.get("section", "general")).lower()
        if section not in SECTION_ORDER:
            warnings.append("Screenshot '%s' has unknown section '%s'; placed under 'general'. "
                            "Valid sections: %s." % (shot["id"], section, ", ".join(SECTION_ORDER)))
        if not str(shot.get("caption", "")).strip():
            warnings.append("Screenshot '%s' has no caption." % shot["id"])
    return errors, warnings


def _dict_items(value: Any) -> List[Dict[str, Any]]:
    """Safe iteration helper: list of dicts, otherwise empty."""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


# --------------------------------------------------------------------------
# Evidence (figures)
# --------------------------------------------------------------------------

@dataclass
class Figure:
    """One image slot in the report (architecture diagram or screenshot)."""
    number: int
    id: str
    caption: str
    section: str
    kind: str                 # "Diagram" or "Screenshot"
    raw_path: str             # path exactly as written in the config
    path: Optional[Path]      # resolved path (None when no path was given)
    exists: bool
    problem: str = ""         # why the image is unavailable


def build_figures(cfg: Dict[str, Any], base_dir: Path, warnings: List[str]) -> Dict[str, Figure]:
    """Resolve every image path, check it is readable and assign figure numbers."""
    entries: List[Dict[str, Any]] = []
    arch = cfg.get("architecture") or {}
    if str(arch.get("diagram", "")).strip():
        entries.append({
            "id": "architecture-diagram",
            "path": arch["diagram"],
            "caption": arch.get("diagram_caption") or "Architecture diagram",
            "section": "architecture",
            "kind": "Diagram",
        })
    for shot in _dict_items(cfg.get("screenshots")):
        entries.append({
            "id": str(shot["id"]),
            "path": shot.get("path", ""),
            "caption": shot.get("caption") or "(no caption supplied)",
            "section": str(shot.get("section", "general")).lower(),
            "kind": "Screenshot",
        })

    def sort_key(pair: Tuple[int, Dict[str, Any]]) -> Tuple[int, int]:
        idx, entry = pair
        section = entry["section"] if entry["section"] in SECTION_ORDER else "general"
        return SECTION_ORDER.index(section), idx

    ordered = [e for _, e in sorted(enumerate(entries), key=sort_key)]
    figures: Dict[str, Figure] = {}
    for number, entry in enumerate(ordered, start=1):
        raw = str(entry["path"] or "").strip()
        section = entry["section"] if entry["section"] in SECTION_ORDER else "general"
        path: Optional[Path] = None
        exists = False
        problem = ""
        if not raw:
            problem = "no file path supplied"
        else:
            # Accept Windows-style backslashes on any platform.
            candidate = Path(raw.replace("\\", "/"))
            path = candidate if candidate.is_absolute() else (base_dir / candidate)
            if not path.is_file():
                problem = "file not found"
            elif path.suffix.lower() not in IMAGE_EXTENSIONS:
                problem = "unsupported image type (use PNG or JPG)"
            else:
                try:
                    ImageReader(str(path)).getSize()
                    exists = True
                except Exception as exc:  # corrupt or unreadable image
                    problem = "unreadable image (%s)" % type(exc).__name__
        if problem:
            warnings.append("%s '%s': %s (%s) - shown as Not Verified placeholder."
                            % (entry["kind"], entry["id"], problem, raw or "no path"))
        figures[entry["id"]] = Figure(number, entry["id"], str(entry["caption"]), section,
                                      entry["kind"], raw, path, exists, problem)
    return figures


def has_evidence(refs: Any, figures: Dict[str, Figure]) -> bool:
    """True if at least one referenced figure exists as a readable file."""
    if not isinstance(refs, list):
        return False
    return any(str(r) in figures and figures[str(r)].exists for r in refs)


def resolve_test_status(test: Dict[str, Any], figures: Dict[str, Figure]) -> Tuple[str, str]:
    """Return (effective status, note). 'Passed' requires evidence."""
    declared = normalize_test_status(test.get("status"))
    evidence = has_evidence(test.get("screenshots", []), figures)
    if declared == PASSED:
        if evidence:
            return PASSED, ""
        return NOT_VERIFIED, "Recorded as Passed in the config, but no evidence screenshot was found."
    if declared == FAILED:
        return FAILED, ("" if evidence else "Failure recorded without an evidence screenshot.")
    return NOT_VERIFIED, ""


def resolve_cleanup_status(step: Dict[str, Any], figures: Dict[str, Figure]) -> Tuple[str, str]:
    """Return (effective status, note). 'Deleted' requires evidence."""
    declared = normalize_cleanup_status(step.get("status"))
    if declared == DELETED:
        if has_evidence(step.get("screenshots", []), figures):
            return DELETED, ""
        return NOT_VERIFIED, "Recorded as deleted in the config, but no evidence screenshot was found."
    return NOT_VERIFIED, ""


# --------------------------------------------------------------------------
# PDF building blocks
# --------------------------------------------------------------------------

def build_styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    styles: Dict[str, ParagraphStyle] = {}
    styles["Body"] = ParagraphStyle("Body", parent=base["BodyText"], fontName="Helvetica",
                                    fontSize=10, leading=14, spaceAfter=6)
    # Names H1 / H2 are used by the table-of-contents logic - do not rename.
    styles["H1"] = ParagraphStyle("H1", parent=base["Heading1"], fontName="Helvetica-Bold",
                                  fontSize=18, leading=22, textColor=NAVY,
                                  spaceBefore=4, spaceAfter=10)
    styles["H2"] = ParagraphStyle("H2", parent=base["Heading2"], fontName="Helvetica-Bold",
                                  fontSize=13, leading=16, textColor=NAVY,
                                  spaceBefore=10, spaceAfter=6)
    styles["H3"] = ParagraphStyle("H3", parent=base["Heading3"], fontName="Helvetica-Bold",
                                  fontSize=11, leading=14, textColor=colors.black,
                                  spaceBefore=8, spaceAfter=4)
    styles["Cell"] = ParagraphStyle("Cell", parent=styles["Body"], fontSize=8.5, leading=11, spaceAfter=0)
    styles["CellHead"] = ParagraphStyle("CellHead", parent=styles["Cell"], fontName="Helvetica-Bold",
                                        textColor=colors.white)
    styles["CellBold"] = ParagraphStyle("CellBold", parent=styles["Cell"], fontName="Helvetica-Bold")
    styles["Mono"] = ParagraphStyle("Mono", parent=styles["Cell"], fontName="Courier", fontSize=8)
    styles["Caption"] = ParagraphStyle("Caption", parent=styles["Body"], fontSize=9, leading=12,
                                       alignment=TA_CENTER, textColor=TEXT_GREY, spaceBefore=4)
    styles["Small"] = ParagraphStyle("Small", parent=styles["Body"], fontSize=8, leading=10,
                                     textColor=TEXT_GREY)
    styles["Bullet"] = ParagraphStyle("Bullet", parent=styles["Body"], leftIndent=14,
                                      bulletIndent=2, spaceAfter=3)
    styles["TOCTitle"] = ParagraphStyle("TOCTitle", parent=styles["H1"])
    styles["CoverKicker"] = ParagraphStyle("CoverKicker", parent=styles["Body"], fontSize=12,
                                           textColor=TEXT_GREY)
    styles["CoverTitle"] = ParagraphStyle("CoverTitle", parent=styles["Body"], fontName="Helvetica-Bold",
                                          fontSize=34, leading=40, textColor=NAVY, spaceAfter=8)
    styles["CoverSub"] = ParagraphStyle("CoverSub", parent=styles["Body"], fontSize=16, leading=20,
                                        textColor=TEXT_GREY, spaceAfter=18)
    return styles


def cell(value: Any, style: ParagraphStyle) -> Any:
    """Convert a table cell value into a flowable."""
    if hasattr(value, "wrap"):  # already a flowable
        return value
    if isinstance(value, Markup):
        return Paragraph(value, style)
    return Paragraph(val(value), style)


def make_table(headers: List[str], rows: List[List[Any]], fractions: List[float],
               styles: Dict[str, ParagraphStyle]) -> Table:
    """Standard report table with a navy header row and zebra striping."""
    data = [[Paragraph(esc(h), styles["CellHead"]) for h in headers]]
    for row in rows:
        data.append([cell(c, styles["Cell"]) for c in row])
    table = Table(data, colWidths=[f * CONTENT_W for f in fractions], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID_GREY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def kv_table(rows: List[Tuple[str, Any]], styles: Dict[str, ParagraphStyle],
             left_fraction: float = 0.28) -> Table:
    """Two-column key/value table without a header row."""
    data = [[Paragraph(esc(k), styles["CellBold"]), cell(v, styles["Cell"])] for k, v in rows]
    table = Table(data, colWidths=[left_fraction * CONTENT_W, (1 - left_fraction) * CONTENT_W],
                  hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID_GREY),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def callout(text: str, styles: Dict[str, ParagraphStyle], kind: str = "warn") -> Table:
    """A shaded notice box (amber = warning, blue = information)."""
    bg, border = (("#fff4e0", "#b26a00") if kind == "warn" else ("#e8f0fa", "#1f3a5f"))
    table = Table([[Paragraph(text, styles["Cell"])]], colWidths=[CONTENT_W], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor(border)),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def make_canvas_class(header_left: str, header_right: str, footer_left: str):
    """Return a canvas class that draws header/footer and 'Page X of Y'."""

    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_states: List[Dict[str, Any]] = []

        def showPage(self):
            self._saved_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_states)
            for state in self._saved_states:
                self.__dict__.update(state)
                self._draw_chrome(total)
                super().showPage()
            super().save()

        def _draw_chrome(self, total: int) -> None:
            page = self._pageNumber
            if page == 1:  # cover page has no header/footer
                return
            self.saveState()
            self.setStrokeColor(GRID_GREY)
            self.setFillColor(TEXT_GREY)
            self.setFont("Helvetica", 8)
            top = PAGE_H - 1.3 * cm
            self.drawString(MARGIN, top, header_left)
            self.drawRightString(PAGE_W - MARGIN, top, header_right)
            self.line(MARGIN, top - 4, PAGE_W - MARGIN, top - 4)
            bottom = 1.3 * cm
            self.line(MARGIN, bottom + 10, PAGE_W - MARGIN, bottom + 10)
            self.drawString(MARGIN, bottom, footer_left)
            self.drawRightString(PAGE_W - MARGIN, bottom, "Page %d of %d" % (page, total))
            self.restoreState()

    return NumberedCanvas


class ReportDoc(BaseDocTemplate):
    """Document template with a cover page layout and a body layout."""

    def __init__(self, filename: str, **kwargs):
        super().__init__(filename, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=2.2 * cm, bottomMargin=2.0 * cm, **kwargs)
        cover = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id="cover")
        body = Frame(MARGIN, 2.0 * cm, CONTENT_W, PAGE_H - 2.2 * cm - 2.0 * cm, id="body")
        self.addPageTemplates([PageTemplate(id="Cover", frames=[cover]),
                               PageTemplate(id="Body", frames=[body])])

    def afterFlowable(self, flowable):
        """Register headings for the table of contents and PDF outline."""
        key = getattr(flowable, "_bookmark", None)
        if key is None:
            return
        level = 0 if flowable.style.name == "H1" else 1
        text = flowable.getPlainText()
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(text, key, level, closed=False)
        self.notify("TOCEntry", (level, text, self.page, key))


# --------------------------------------------------------------------------
# Report content
# --------------------------------------------------------------------------

class ReportBuilder:
    """Turns the validated configuration into a list of flowables."""

    def __init__(self, cfg: Dict[str, Any], base_dir: Path, warnings: List[str]):
        self.cfg = cfg
        self.warnings = warnings
        self.styles = build_styles()
        self.figures = build_figures(cfg, base_dir, warnings)
        self.project = cfg["project"]
        self._n1 = 0
        self._n2 = 0
        self._hid = 0

        self.test_results: List[Tuple[Dict[str, Any], str, str]] = [
            (t,) + resolve_test_status(t, self.figures) for t in _dict_items(cfg.get("tests"))
        ]
        steps = _dict_items((cfg.get("cleanup") or {}).get("steps"))
        if not steps:
            steps = [{"action": a, "resource": r, "status": "Not Verified", "screenshots": []}
                     for a, r in DEFAULT_CLEANUP_STEPS]
        self.cleanup_results: List[Tuple[Dict[str, Any], str, str]] = [
            (s,) + resolve_cleanup_status(s, self.figures) for s in steps
        ]

    # ---- small helpers ----------------------------------------------------

    def _heading(self, text: str, style: str) -> Paragraph:
        self._hid += 1
        para = Paragraph(esc(text), self.styles[style])
        para._bookmark = "sec%d" % self._hid  # picked up by ReportDoc.afterFlowable
        return para

    def h1(self, title: str, numbered: bool = True) -> List[Any]:
        if numbered:
            self._n1 += 1
            self._n2 = 0
            title = "%d. %s" % (self._n1, title)
        return [CondPageBreak(7 * cm), self._heading(title, "H1")]

    def h2(self, title: str) -> List[Any]:
        self._n2 += 1
        return [CondPageBreak(4 * cm), self._heading("%d.%d %s" % (self._n1, self._n2, title), "H2")]

    def para(self, text: Any) -> List[Any]:
        """One Paragraph per blank-line separated block."""
        blocks = [b.strip() for b in str(text).split("\n\n") if b.strip()]
        return [Paragraph(esc(b), self.styles["Body"]) for b in blocks]

    def bullets(self, items: List[str]) -> List[Any]:
        return [Paragraph(esc(i), self.styles["Bullet"], bulletText="\u2022") for i in items]

    def note(self, text: str) -> Paragraph:
        return Paragraph(esc(text), self.styles["Small"])

    def fig_refs(self, ids: Any) -> Markup:
        if not isinstance(ids, list) or not ids:
            return val("", "None supplied")
        parts = []
        for ident in ids:
            fig = self.figures.get(str(ident))
            if fig is None:
                parts.append("%s (unknown id)" % esc(ident))
            elif fig.exists:
                parts.append("Figure %d" % fig.number)
            else:
                parts.append("Figure %d (file missing)" % fig.number)
        return Markup(", ".join(parts))

    def figure_block(self, fig: Figure) -> List[Any]:
        caption = Paragraph("<b>Figure %d:</b> %s" % (fig.number, esc(fig.caption)), self.styles["Caption"])
        if fig.exists and fig.path is not None:
            width, height = ImageReader(str(fig.path)).getSize()
            scale = min(CONTENT_W / width, (15 * cm) / height, 1.0)
            img = Image(str(fig.path), width=width * scale, height=height * scale)
            frame = Table([[img]], colWidths=[width * scale + 8], hAlign="CENTER")
            frame.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.6, GRID_GREY),
                ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            return [KeepTogether([frame, caption])]
        text = ("<b>NOT VERIFIED</b> - %s not available<br/>"
                "Reason: %s<br/>Path in config: %s"
                % (fig.kind.lower(), esc(fig.problem or "unknown"), esc(fig.raw_path or "(none)")))
        box = Table([[Paragraph(text, self.styles["Cell"])]], colWidths=[CONTENT_W], rowHeights=[2.6 * cm])
        box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff4e0")),
            ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#b26a00")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ]))
        return [KeepTogether([box, caption])]

    def figures_for(self, section: str) -> List[Any]:
        out: List[Any] = []
        for fig in self.figures.values():
            if fig.section == section:
                out.append(Spacer(1, 8))
                out.extend(self.figure_block(fig))
        return out

    # ---- counts -------------------------------------------------------------

    def test_counts(self) -> Dict[str, int]:
        counts = {PASSED: 0, FAILED: 0, NOT_VERIFIED: 0}
        for _, status, _ in self.test_results:
            counts[status] += 1
        return counts

    def cleanup_verified(self) -> int:
        return sum(1 for _, status, _ in self.cleanup_results if status == DELETED)

    # ---- front matter -------------------------------------------------------

    def cover(self) -> List[Any]:
        p, s = self.project, self.styles
        counts = self.test_counts()
        total = len(self.test_results)
        rows: List[Tuple[str, Any]] = [
            ("Student", val(p.get("student"))),
            ("Project", val(p.get("name"))),
            ("Region", val("%s (%s)" % (p["region"], p["region_label"]) if p.get("region_label") else p["region"])),
        ]
        if str(p.get("course", "")).strip():
            rows.append(("Course", val(p["course"])))
        if str(p.get("institution", "")).strip():
            rows.append(("Institution", val(p["institution"])))
        rows.append(("Report date", val(self.report_date())))
        rows.append(("Test verification",
                     val("%d of %d tests verified as Passed with evidence" % (counts[PASSED], total))
                     if total else val("No tests defined")))
        return [
            Spacer(1, 4.5 * cm),
            Paragraph("AWS CLOUD NETWORKING PROJECT", s["CoverKicker"]),
            Spacer(1, 6),
            Paragraph(esc(p.get("name", "AWS VPC Peering")), s["CoverTitle"]),
            Paragraph("Implementation and Verification Report", s["CoverSub"]),
            HRFlowable(width="100%", thickness=2, color=NAVY, spaceAfter=18),
            kv_table(rows, s, 0.30),
            Spacer(1, 3 * cm),
            Paragraph("Generated automatically by generate_report.py from project_config.json. "
                      "Results that are not backed by supplied evidence are marked <b>Not Verified</b>.",
                      s["Small"]),
            NextPageTemplate("Body"),
            PageBreak(),
        ]

    def report_date(self) -> str:
        raw = str(self.project.get("report_date", "")).strip()
        return raw if raw else dt.date.today().strftime("%d %B %Y")

    def toc(self) -> List[Any]:
        toc = TableOfContents()
        toc.levelStyles = [
            ParagraphStyle("TOC1", fontName="Helvetica-Bold", fontSize=11, leading=14,
                           leftIndent=0, spaceBefore=4),
            ParagraphStyle("TOC2", fontName="Helvetica", fontSize=9.5, leading=11.5,
                           leftIndent=18, spaceBefore=0),
        ]
        return [Paragraph("Table of Contents", self.styles["TOCTitle"]), Spacer(1, 6), toc, PageBreak()]

    # ---- sections -------------------------------------------------------------

    def sec_summary(self) -> List[Any]:
        f = self.h1("Executive Summary")
        arch = self.cfg.get("architecture") or {}
        vpcs = _dict_items(arch.get("vpcs"))
        intro = "This report documents the AWS VPC peering project of %s in the %s region" % (
            self.project["student"], self.project["region"])
        if self.project.get("region_label"):
            intro += " (%s)" % self.project["region_label"]
        if len(vpcs) >= 2:
            intro += ", connecting %s (%s) and %s (%s)" % (
                vpcs[0]["name"], vpcs[0].get("cidr", "CIDR not supplied"),
                vpcs[1]["name"], vpcs[1].get("cidr", "CIDR not supplied"))
        intro += "."
        f += self.para(intro)
        if str(self.cfg.get("summary", "")).strip():
            f += self.para(self.cfg["summary"])

        counts = self.test_counts()
        total = len(self.test_results)
        shots = [fg for fg in self.figures.values() if fg.kind == "Screenshot"]
        shots_ok = sum(1 for fg in shots if fg.exists)
        cleanup_total = len(self.cleanup_results)
        f += self.h2("Verification Summary")
        f.append(kv_table([
            ("Tests defined", val(str(total) if total else "", "None defined")),
            ("Passed (with evidence)", status_markup("%d" % counts[PASSED])),
            ("Failed", status_markup("%d" % counts[FAILED]) if counts[FAILED] else Markup("0")),
            ("Not Verified", status_markup("%d" % counts[NOT_VERIFIED])),
            ("Screenshots supplied", val("%d of %d listed" % (shots_ok, len(shots)))),
            ("Cleanup steps with evidence", val("%d of %d" % (self.cleanup_verified(), cleanup_total))),
        ], self.styles, 0.40))
        f.append(Spacer(1, 6))
        if counts[NOT_VERIFIED] or self.cleanup_verified() < cleanup_total:
            f.append(callout("<b>Evidence notice:</b> items marked <b>Not Verified</b> have no supporting "
                             "evidence in the supplied configuration. They must not be read as passed or "
                             "completed.", self.styles))
        return f

    def sec_objectives(self) -> List[Any]:
        f = self.h1("Project Objectives")
        items = [str(i) for i in self.cfg.get("objectives", []) or []]
        f += self.bullets(items) if items else [self.note("No objectives were supplied in the configuration.")]
        return f

    def sec_architecture(self) -> List[Any]:
        f = self.h1("Architecture")
        arch = self.cfg.get("architecture") or {}
        if str(arch.get("description", "")).strip():
            f += self.para(arch["description"])
        vpcs = _dict_items(arch.get("vpcs"))
        if vpcs:
            f += self.h2("VPC Design")
            f.append(make_table(["VPC", "CIDR block", "Notes"],
                                [[v.get("name"), v.get("cidr"), v.get("notes")] for v in vpcs],
                                [0.25, 0.25, 0.50], self.styles))
        subnets = _dict_items(arch.get("subnets"))
        if subnets:
            f += self.h2("Subnet Design")
            f.append(make_table(["Subnet", "CIDR block", "VPC", "Type"],
                                [[s.get("name"), s.get("cidr"), s.get("vpc"), s.get("type")] for s in subnets],
                                [0.30, 0.22, 0.22, 0.26], self.styles))
        instances = _dict_items(arch.get("instances"))
        if instances:
            f += self.h2("Instances")
            f.append(make_table(["Instance", "Private IP", "Subnet", "VPC", "Role"],
                                [[i.get("name"), i.get("private_ip"), i.get("subnet"), i.get("vpc"), i.get("role")]
                                 for i in instances],
                                [0.18, 0.17, 0.22, 0.15, 0.28], self.styles))
        f += self.h2("Diagram and Evidence")
        figs = self.figures_for("architecture")
        f += figs if figs else [self.note("No architecture diagram or evidence was supplied.")]
        return f

    def sec_resources(self) -> List[Any]:
        f = self.h1("Resource Inventory")
        resources = _dict_items(self.cfg.get("resources"))
        if not resources:
            return f + [self.note("No resources were supplied in the configuration.")]
        f += self.para("Resource identifiers are reproduced as supplied. An empty identifier means "
                       "the ID was not recorded; it does not imply the resource does or does not exist.")
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for res in resources:
            groups.setdefault(str(res.get("category") or "Other"), []).append(res)
        for category, items in groups.items():
            f += self.h2(category)
            f.append(make_table(["Name", "Resource ID", "CIDR / IP address", "Details"],
                                [[r.get("name"), r.get("resource_id"), r.get("address"), r.get("details")]
                                 for r in items],
                                [0.26, 0.24, 0.20, 0.30], self.styles))
        f += self.figures_for("resources")
        return f

    def sec_routing(self) -> List[Any]:
        f = self.h1("Routing Configuration")
        tables = _dict_items(self.cfg.get("route_tables"))
        f.append(callout("Route entries below are reproduced from the project configuration as written by "
                         "the author. The generator cannot check them against the live AWS account; see "
                         "the Test Results section and the screenshots for verification.", self.styles, "info"))
        f.append(Spacer(1, 6))
        if not tables:
            f.append(self.note("No route tables were supplied in the configuration."))
        for rt in tables:
            f += self.h2("%s (%s)" % (rt.get("name"), rt.get("vpc") or "VPC not supplied"))
            f.append(Paragraph("<b>Associated subnets:</b> %s" % val(rt.get("associated_subnets")), self.styles["Body"]))
            routes = _dict_items(rt.get("routes"))
            if routes:
                f.append(make_table(["Destination", "Target", "Notes"],
                                    [[r.get("destination"), r.get("target"), r.get("notes")] for r in routes],
                                    [0.28, 0.32, 0.40], self.styles))
            else:
                f.append(self.note("No routes supplied for this route table."))
        f += self.figures_for("routing")
        return f

    def sec_security(self) -> List[Any]:
        f = self.h1("Security Configuration")
        groups = _dict_items(self.cfg.get("security_groups"))
        f.append(callout("Security rules are reproduced from the project configuration as supplied by the "
                         "author and have not been checked against the live AWS account.", self.styles, "info"))
        f.append(Spacer(1, 6))
        if not groups:
            f.append(self.note("No security groups were supplied in the configuration."))
        for sg in groups:
            f += self.h2("Security group: %s" % sg.get("name"))
            f.append(Paragraph("<b>VPC:</b> %s &nbsp;&nbsp; <b>Attached to:</b> %s"
                               % (val(sg.get("vpc")), val(sg.get("attached_to"))), self.styles["Body"]))
            inbound = _dict_items(sg.get("inbound"))
            outbound = _dict_items(sg.get("outbound"))
            f.append(Paragraph("<b>Inbound rules</b>", self.styles["Body"]))
            f.append(make_table(["Protocol", "Port", "Source", "Description"],
                                [[r.get("protocol"), r.get("port"), r.get("source"), r.get("description")]
                                 for r in inbound], [0.14, 0.12, 0.30, 0.44], self.styles)
                     if inbound else self.note("No inbound rules supplied."))
            f.append(Spacer(1, 4))
            f.append(Paragraph("<b>Outbound rules</b>", self.styles["Body"]))
            f.append(make_table(["Protocol", "Port", "Destination", "Description"],
                                [[r.get("protocol"), r.get("port"), r.get("destination"), r.get("description")]
                                 for r in outbound], [0.14, 0.12, 0.30, 0.44], self.styles)
                     if outbound else self.note("No outbound rules supplied."))
        nacls = _dict_items(self.cfg.get("nacls"))
        if nacls:
            f += self.h2("Network ACLs")
            f.append(make_table(["Name", "Subnet(s)", "Notes"],
                                [[n.get("name"), n.get("subnet"), n.get("notes")] for n in nacls],
                                [0.28, 0.27, 0.45], self.styles))
        f += self.figures_for("security")
        return f

    def sec_tests(self) -> List[Any]:
        f = self.h1("Test Results")
        if not self.test_results:
            return f + [self.note("No tests were defined in the configuration. Connectivity is Not Verified.")]
        counts = self.test_counts()
        f += self.para("%d test(s) defined: %d Passed (with evidence), %d Failed, %d Not Verified. "
                       "A test is reported as Passed only when a referenced evidence screenshot exists."
                       % (len(self.test_results), counts[PASSED], counts[FAILED], counts[NOT_VERIFIED]))
        if counts[NOT_VERIFIED]:
            f.append(callout("<b>Not Verified:</b> %d test(s) have no result with supporting evidence. "
                             "Connectivity for those paths has not been demonstrated by this report."
                             % counts[NOT_VERIFIED], self.styles))
            f.append(Spacer(1, 6))
        f += self.h2("Summary Table")
        rows = []
        for test, status, _ in self.test_results:
            rows.append([test.get("id"),
                         test.get("name"),
                         Markup("%s<br/>to %s" % (val(test.get("source")), val(test.get("destination")))),
                         test.get("expected"),
                         status_markup(status),
                         self.fig_refs(test.get("screenshots"))])
        f.append(make_table(["ID", "Test", "Path", "Expected", "Status", "Evidence"], rows,
                            [0.06, 0.22, 0.24, 0.17, 0.13, 0.18], self.styles))
        f += self.h2("Test Details")
        for test, status, note in self.test_results:
            observed = test.get("observed_output")
            observed_cell = (Paragraph(esc(observed), self.styles["Mono"])
                             if str(observed or "").strip() else val("", "Not supplied"))
            command = test.get("command")
            command_cell = (Paragraph(esc(command), self.styles["Mono"])
                            if str(command or "").strip() else val("", "Not supplied"))
            status_text = Markup(status_markup(status) + ((" - " + esc(note)) if note else ""))
            rows_kv: List[Tuple[str, Any]] = [
                ("Source", val(test.get("source"))),
                ("Destination", val(test.get("destination"))),
                ("Command", command_cell),
                ("Expected result", val(test.get("expected"))),
                ("Observed output (as supplied)", observed_cell),
                ("Status", status_text),
                ("Evidence", self.fig_refs(test.get("screenshots"))),
            ]
            if str(test.get("notes", "")).strip():
                rows_kv.append(("Notes", val(test["notes"])))
            f.append(KeepTogether([Paragraph(esc("%s - %s" % (test.get("id"), test.get("name"))), self.styles["H3"]),
                                   kv_table(rows_kv, self.styles, 0.28)]))
        f += self.figures_for("tests")
        return f

    def sec_troubleshooting(self) -> List[Any]:
        f = self.h1("Troubleshooting")
        f += self.h2("Issues Encountered")
        issues = _dict_items(self.cfg.get("troubleshooting"))
        if issues:
            rows = [[str(i), t.get("issue"), t.get("cause"), t.get("resolution"), self.fig_refs(t.get("screenshots"))]
                    for i, t in enumerate(issues, start=1)]
            f.append(make_table(["#", "Issue", "Cause", "Resolution", "Evidence"], rows,
                                [0.05, 0.25, 0.25, 0.27, 0.18], self.styles))
        else:
            f.append(self.note("No issues were recorded by the author. Nothing has been added automatically."))
        f += self.h2("Reference Checklist")
        f += self.para("General guidance for VPC peering problems. This is a generic checklist, not a "
                       "record of problems that occurred in this project.")
        f += self.bullets(TROUBLESHOOTING_REFERENCE)
        f += self.figures_for("troubleshooting")
        return f

    def sec_lessons(self) -> List[Any]:
        f = self.h1("Lessons Learned")
        lessons = [str(x) for x in self.cfg.get("lessons_learned", []) or []]
        f += self.bullets(lessons) if lessons else [
            self.note("No lessons learned were recorded by the author. Nothing has been added automatically.")]
        return f

    def sec_cleanup(self) -> List[Any]:
        f = self.h1("Cleanup Documentation")
        verified = self.cleanup_verified()
        total = len(self.cleanup_results)
        if verified == 0:
            f.append(callout("<b>Cleanup NOT verified.</b> No cleanup evidence was supplied, so this report does "
                             "not claim that any AWS resource has been deleted. Resources that still exist can "
                             "continue to incur charges (for example instances, Elastic IPs and NAT gateways).",
                             self.styles))
        elif verified < total:
            f.append(callout("<b>Cleanup partially verified:</b> %d of %d steps have evidence. "
                             "The remaining steps are Not Verified." % (verified, total), self.styles))
        else:
            f.append(callout("All %d cleanup steps have supporting evidence." % total, self.styles, "info"))
        f.append(Spacer(1, 6))
        f += self.para("Recommended order: delete dependent resources first so that the remaining deletions "
                       "are not blocked.")
        rows = []
        for idx, (step, status, note) in enumerate(self.cleanup_results, start=1):
            status_text = Markup(status_markup(status) + (("<br/>" + esc(note)) if note else ""))
            rows.append([str(idx), step.get("action"), step.get("resource"), status_text,
                         self.fig_refs(step.get("screenshots"))])
        f.append(make_table(["#", "Action", "Resource", "Status", "Evidence"], rows,
                            [0.05, 0.30, 0.27, 0.22, 0.16], self.styles))
        f += self.figures_for("cleanup")
        return f

    def sec_conclusion(self) -> List[Any]:
        f = self.h1("Conclusion")
        counts = self.test_counts()
        total = len(self.test_results)
        if total == 0:
            text = "No tests were defined, so connectivity between the two VPCs is Not Verified by this report."
        elif counts[PASSED] == total:
            text = "All %d defined tests are recorded as Passed with supporting screenshot evidence." % total
        else:
            text = ("%d of %d defined tests are verified as Passed. %d are Not Verified and %d Failed. "
                    "Only the verified results should be treated as demonstrated."
                    % (counts[PASSED], total, counts[NOT_VERIFIED], counts[FAILED]))
        f += self.para(text)
        if str(self.cfg.get("conclusion_notes", "")).strip():
            f += self.para(self.cfg["conclusion_notes"])
        return f

    def sec_additional(self) -> List[Any]:
        figs = self.figures_for("general")
        if not figs:
            return []
        return self.h1("Additional Evidence") + figs

    def appendices(self) -> List[Any]:
        f = self.h1("Appendix A: Evidence Register", numbered=False)
        if self.figures:
            rows = []
            for fig in self.figures.values():
                state = (Markup('<font color="#1b7f3b"><b>Provided</b></font>') if fig.exists
                         else Markup('<font color="#b26a00"><b>Missing</b></font> (%s)' % esc(fig.problem)))
                rows.append(["Figure %d" % fig.number, fig.kind, fig.section, fig.caption, fig.raw_path, state])
            f.append(make_table(["Figure", "Type", "Section", "Caption", "File (as in config)", "Status"], rows,
                                [0.10, 0.12, 0.13, 0.25, 0.22, 0.18], self.styles))
        else:
            f.append(self.note("No images were listed in the configuration."))
        f += self.h1("Appendix B: Data Completeness Notes", numbered=False)
        if self.warnings:
            f += self.bullets(self.warnings)
        else:
            f.append(self.note("No completeness warnings were raised while generating this report."))
        return f

    # ---- assembly -------------------------------------------------------------

    def story(self) -> List[Any]:
        story: List[Any] = []
        story += self.cover()
        story += self.toc()
        # Sections must be built in document order so heading numbers are sequential.
        for section in (self.sec_summary, self.sec_objectives, self.sec_architecture,
                        self.sec_resources, self.sec_routing, self.sec_security, self.sec_tests,
                        self.sec_troubleshooting, self.sec_lessons, self.sec_cleanup,
                        self.sec_conclusion, self.sec_additional, self.appendices):
            story += section()
        return story

    def build(self, output: Path) -> None:
        p = self.project
        doc = ReportDoc(str(output), title="%s - Project Report" % p["name"], author=p["student"],
                        subject="AWS VPC Peering project report", creator="generate_report.py")
        canvas_cls = make_canvas_class(
            header_left=str(p.get("title") or "%s - Project Report" % p["name"]),
            header_right="Region: %s" % p["region"],
            footer_left="%s | %s" % (p["student"], p["name"]),
        )
        doc.multiBuild(self.story(), canvasmaker=canvas_cls)


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the AWS VPC Peering PDF report.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG),
                        help="Path to the JSON configuration (default: project_config.json next to this script).")
    parser.add_argument("--output", default=None,
                        help="PDF output path (default: output/%s next to this script)." % DEFAULT_OUTPUT_NAME)
    parser.add_argument("--force", action="store_true",
                        help="Overwrite the output file if it already exists.")
    parser.add_argument("--validate-only", action="store_true",
                        help="Check the configuration and images, then exit without creating a PDF.")
    parser.add_argument("--debug", action="store_true", help="Show full error tracebacks.")
    return parser.parse_args(argv)


def resolve_output_path(arg: Optional[str], force: bool) -> Path:
    """Pick the output path; never overwrite an existing file unless --force."""
    out = Path(arg) if arg else DEFAULT_OUTPUT_DIR / DEFAULT_OUTPUT_NAME
    if out.suffix.lower() != ".pdf":
        out = out.with_suffix(".pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not force:
        counter = 1
        while True:
            candidate = out.with_name("%s_%d%s" % (out.stem, counter, out.suffix))
            if not candidate.exists():
                print("Note: %s already exists; writing to %s instead (use --force to overwrite)."
                      % (out.name, candidate.name))
                return candidate
            counter += 1
    return out


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        config_path = Path(args.config).expanduser().resolve()
        cfg = load_config(config_path)
        errors, warnings = validate_config(cfg)
        if errors:
            print("Configuration errors (%d):" % len(errors))
            for err in errors:
                print("  - " + err)
            return 2
        builder = ReportBuilder(cfg, config_path.parent, warnings)
        if warnings:
            print("Warnings (%d):" % len(builder.warnings))
            for warn in builder.warnings:
                print("  - " + warn)
        if args.validate_only:
            print("Validation finished: configuration is valid.")
            return 0
        output = resolve_output_path(args.output, args.force)
        builder.build(output)
        counts = builder.test_counts()
        print("Report written to: %s" % output)
        print("Tests - Passed: %d, Failed: %d, Not Verified: %d | Cleanup steps with evidence: %d of %d"
              % (counts[PASSED], counts[FAILED], counts[NOT_VERIFIED],
                 builder.cleanup_verified(), len(builder.cleanup_results)))
        return 0
    except ConfigError as exc:
        print("Configuration problem: %s" % exc)
        return 2
    except PermissionError as exc:
        print("Cannot write the PDF (is it open in a PDF viewer?): %s" % exc)
        return 3
    except Exception as exc:  # last-resort handler with a readable message
        print("Unexpected error: %s: %s" % (type(exc).__name__, exc))
        if args.debug:
            traceback.print_exc()
        else:
            print("Run again with --debug for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

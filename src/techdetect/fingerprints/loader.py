"""Load fingerprint rules into one normalised form.

Rules come in webappanalyzer's JSON schema (the open continuation of Wappalyzer's rule
set), from three places:
  fingerprints/webappanalyzer/   the pinned upstream copy (fingerprints/VERSION)
  fingerprints/custom/           our own rules, same schema, always loaded
  fingerprints/candidates/       proposed rules awaiting review, loaded only on request,
                                 and only those marked "proposed" or "accepted"

Parsing copies Wappalyzer's own `transformPatterns` / `parsePattern` (wappalyzer.js,
npm package wappalyzer@6.10.66), so every rule means here what its author meant:
  - a field given as a string or a list becomes a list of patterns;
  - in name -> pattern fields (headers, cookies, meta, dns) names are lowercased;
    `js` chains and `dom` selectors keep their case;
  - a `dom` list of selectors means "this element exists";
  - "regex\\;version:\\1\\;confidence:50": split on `\\;`, each tag on its first `:`;
    confidence defaults to 100, version to "";
  - regexes are case-insensitive.

Nothing is fixed silently: a pattern that cannot be used is skipped and listed in the
load report with its reason.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import regex

# Fields holding a list of patterns, matched against one kind of text each.
LIST_FIELDS = ("url", "html", "text", "css", "scriptSrc", "scripts", "xhr", "robots", "certIssuer")
# Fields holding name -> pattern(s): header name, cookie name, meta name, JS chain, DNS type.
DICT_FIELDS = ("headers", "cookies", "meta", "js", "dns")
CASE_SENSITIVE_KEYS = frozenset({"js"})  # as in Wappalyzer; dom selectors also keep case
RELATION_FIELDS = ("implies", "requires", "excludes", "requiresCategory")
# Read (cats, website) or deliberately unused descriptive fields: never reported.
METADATA_FIELDS = frozenset(
    {"cats", "website", "description", "icon", "cpe", "saas", "oss", "pricing"}
)
# Review fields of candidate rules (status, sources...): not part of the rule itself.
REVIEW_FIELDS = frozenset(
    {
        "status",
        "seen_on",
        "examples",
        "source_url",
        "source_quote",
        "source_verified",
        "origin",
        "review_note",
        "reviewed_by",
        "reviewed_at",
    }
)
DOM_PARTS = ("exists", "text", "attributes", "properties")
KNOWN_TAGS = frozenset({"version", "confidence"})
CANDIDATE_STATUSES = frozenset({"proposed", "accepted"})  # never "rejected"
TAG_SEPARATOR = "\\;"  # the two characters backslash + semicolon


@dataclass(frozen=True)
class Pattern:
    """One clue: where to look (field/key/subkey) and what to look for (regex)."""

    field: str  # "html", "headers", "dom", ...
    key: str | None  # header/cookie/meta name, JS chain, DNS type, dom selector
    subkey: str | None  # dom only: "exists", "text", "attributes.href", "properties.x"
    source: str  # the rule text exactly as written, quoted in evidence
    regex: regex.Pattern[str]
    version: str  # template such as "\\1" or "\\1?4:", "" when none
    confidence: int  # the rule author's estimate of the clue's strength, default 100


@dataclass(frozen=True)
class Implied:
    name: str
    confidence: int
    version: str


@dataclass(frozen=True)
class Technology:
    name: str
    origin: str  # "webappanalyzer", "custom" or "candidates"
    categories: tuple[int, ...]
    website: str | None
    patterns: tuple[Pattern, ...]
    implies: tuple[Implied, ...] = ()
    requires: tuple[str, ...] = ()
    requires_category: tuple[int, ...] = ()
    excludes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Category:
    name: str
    groups: tuple[int, ...]
    priority: int


@dataclass(frozen=True)
class Skipped:
    technology: str
    field: str
    pattern: str
    reason: str


@dataclass
class LoadReport:
    upstream: dict[str, str] = field(default_factory=dict)  # the VERSION file
    files: Counter[str] = field(default_factory=Counter)  # origin -> files read
    technologies: Counter[str] = field(default_factory=Counter)  # origin -> entries read
    distinct_technologies: int = 0  # after merging same-name entries
    patterns_by_field: Counter[str] = field(default_factory=Counter)
    patterns_by_confidence: Counter[int] = field(default_factory=Counter)
    skipped: list[Skipped] = field(default_factory=list)
    converted: list[Skipped] = field(default_factory=list)  # JS-only escapes rewritten
    ignored_fields: Counter[str] = field(default_factory=Counter)  # field -> techs
    unknown_tags: Counter[str] = field(default_factory=Counter)
    merged: list[str] = field(default_factory=list)  # custom/candidate names already known
    candidates_left_out: Counter[str] = field(default_factory=Counter)  # status -> count
    unreviewed_custom: list[str] = field(default_factory=list)  # custom rules not loaded
    unknown_relations: list[tuple[str, str, str]] = field(default_factory=list)
    unknown_categories: list[tuple[str, int]] = field(default_factory=list)
    candidates_loaded: bool = False

    @property
    def pattern_count(self) -> int:
        return sum(self.patterns_by_field.values())

    def format(self, ruleset_version: str) -> str:
        """The report as printed by `techdetect rules`."""
        up = self.upstream
        lines = [
            f"rules: {ruleset_version}",
            f"upstream: {up.get('source', '?')} commit {up.get('commit', '?')} "
            f"({up.get('commit_date', '?')}), copied {up.get('copied', '?')}",
            "files: "
            + ", ".join(f"{o} {self.files[o]}" for o in ("webappanalyzer", "custom", "candidates"))
            + ("" if self.candidates_loaded else " (candidates not loaded)"),
            f"technologies: {self.distinct_technologies} (entries read: "
            + ", ".join(f"{o} {n}" for o, n in self.technologies.items())
            + ")",
            f"patterns: {self.pattern_count} | "
            + ", ".join(f"{f} {n}" for f, n in self.patterns_by_field.most_common()),
            "patterns per confidence: "
            + " | ".join(
                f"{c}: {n}" for c, n in sorted(self.patterns_by_confidence.items(), reverse=True)
            ),
            f"skipped patterns: {len(self.skipped)}",
        ]
        lines += [f"  {s.technology} [{s.field}] {s.pattern!r}: {s.reason}" for s in self.skipped]
        lines.append(f"patterns with JavaScript-only escapes rewritten: {len(self.converted)}")
        lines += [f"  {s.technology} [{s.field}] {s.pattern!r}: {s.reason}" for s in self.converted]
        if self.unknown_tags:
            lines.append(f"unknown pattern tags (ignored): {dict(self.unknown_tags)}")
        if self.ignored_fields:
            lines.append(f"fields not used (techs having them): {dict(self.ignored_fields)}")
        if self.merged:
            lines.append(f"merged into existing technologies: {', '.join(self.merged)}")
        if self.unreviewed_custom:
            lines.append(
                "custom rules NOT loaded (no reviewed_by: human): "
                + ", ".join(self.unreviewed_custom)
            )
        if self.candidates_left_out:
            lines.append(f"candidates left out, by status: {dict(self.candidates_left_out)}")
        lines.append(f"relations naming an unknown technology: {len(self.unknown_relations)}")
        lines += [f"  {t} {kind} {target!r}" for t, kind, target in self.unknown_relations]
        lines.append(f"unknown category ids: {len(self.unknown_categories)}")
        lines += [f"  {t}: {cat}" for t, cat in self.unknown_categories]
        return "\n".join(lines)


@dataclass(frozen=True)
class RuleSet:
    technologies: dict[str, Technology]
    categories: dict[int, Category]
    groups: dict[int, str]
    version: str  # e.g. "webappanalyzer@9e52f10a9533+custom:none"
    report: LoadReport

    def js_chains(self) -> list[str]:
        """Every JavaScript property chain the rules ask about, e.g. "jQuery.fn.jquery".

        These can only be read while the page is open in the browser, so collection asks
        for all of them and records the list it asked.
        """
        return sorted(
            {
                p.key
                for tech in self.technologies.values()
                for p in tech.patterns
                if p.field == "js" and p.key
            }
        )


# ---------------------------------------------------------------- one pattern


def split_tags(raw: Any) -> tuple[str, dict[str, str]]:
    """ "regex\\;version:\\1\\;confidence:50" -> ("regex", {"version": "\\1", ...}).

    Each tag is split on its first ":" only, so a version template like "\\1?a:b"
    stays whole (Wappalyzer does the same).
    """
    head, *tags = str(raw).split(TAG_SEPARATOR)
    attrs: dict[str, str] = {}
    for tag in tags:
        name, sep, value = tag.partition(":")
        if sep:
            attrs[name] = value
    return head, attrs


# Letters that are real escapes in a JavaScript RegExp (without the `u` flag). Any other
# backslash + letter is just that letter in JavaScript, but can mean something else in
# Python: "\A" is "start of text", "\a" is the bell character.
JS_ESCAPE_LETTERS = frozenset("dDwWsSbBtnvfrcxuk")


def js_regex_to_python(text: str) -> tuple[str, int]:
    """Rewrite JavaScript-only escapes so the pattern means in Python what its author meant.

    Returns the pattern and how many escapes were changed. Only "backslash + letter that
    JavaScript treats as a plain letter" is changed; everything else is kept as written.
    """
    out: list[str] = []
    changed = 0
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            following = text[i + 1]
            if following.isascii() and following.isalpha() and following not in JS_ESCAPE_LETTERS:
                out.append(following)
                changed += 1
            else:
                out.append(char + following)  # also keeps "\\" (an escaped backslash) whole
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out), changed


def parse_pattern(
    raw: Any,
    field_name: str,
    key: str | None,
    subkey: str | None,
    tech: str,
    report: LoadReport,
) -> Pattern | None:
    """One rule string -> Pattern, or None (skipped, with the reason in the report)."""
    text, attrs = split_tags(raw)
    report.unknown_tags.update(name for name in attrs if name not in KNOWN_TAGS)
    try:
        confidence = int(attrs.get("confidence", 100))
    except ValueError:
        report.skipped.append(
            Skipped(
                tech, field_name, str(raw), f"confidence is not a number: {attrs['confidence']!r}"
            )
        )
        return None
    text, changed = js_regex_to_python(text)
    if changed:
        report.converted.append(Skipped(tech, field_name, str(raw), f"{changed} JS escape(s)"))
    try:
        # Rules are written for JavaScript's RegExp; the `regex` module accepts this
        # syntax (lookbehind, named groups...). Anything it rejects is counted, not fixed.
        compiled = regex.compile(text, regex.IGNORECASE)
    except regex.error as exc:
        report.skipped.append(Skipped(tech, field_name, str(raw), f"invalid regex: {exc}"))
        return None
    pattern = Pattern(
        field_name, key, subkey, str(raw), compiled, attrs.get("version", ""), confidence
    )
    report.patterns_by_field[field_name] += 1
    report.patterns_by_confidence[confidence] += 1
    return pattern


def _as_list(value: Any) -> list[Any] | None:
    """A string (or number) or a list of them, as a list; None if it is neither."""
    if isinstance(value, str | int | float):
        return [value]
    if isinstance(value, list):
        return value
    return None


# ---------------------------------------------------------------- one technology


def _patterns(name: str, rule: dict[str, Any], report: LoadReport) -> list[Pattern]:
    out: list[Pattern | None] = []

    def wrong_shape(field_name: str, value: Any) -> None:
        report.skipped.append(Skipped(name, field_name, json.dumps(value)[:80], "unexpected shape"))

    for field_name in LIST_FIELDS:
        if field_name not in rule:
            continue
        values = _as_list(rule[field_name])
        if values is None:
            wrong_shape(field_name, rule[field_name])
            continue
        out += [parse_pattern(v, field_name, None, None, name, report) for v in values]

    for field_name in DICT_FIELDS:
        if field_name not in rule:
            continue
        if not isinstance(rule[field_name], dict):
            wrong_shape(field_name, rule[field_name])
            continue
        for raw_key, raw_values in rule[field_name].items():
            key = raw_key if field_name in CASE_SENSITIVE_KEYS else raw_key.lower()
            values = _as_list(raw_values)
            if values is None:
                wrong_shape(f"{field_name}.{raw_key}", raw_values)
                continue
            out += [parse_pattern(v, field_name, key, None, name, report) for v in values]

    if "dom" in rule:
        out += _dom_patterns(name, rule["dom"], report)
    return [p for p in out if p is not None]


def _dom_patterns(name: str, dom: Any, report: LoadReport) -> list[Pattern | None]:
    # A selector list means "the element exists", exactly as Wappalyzer expands it.
    selectors = _as_list(dom)
    if selectors is not None:
        dom = {selector: {"exists": ""} for selector in selectors}
    if not isinstance(dom, dict):
        report.skipped.append(Skipped(name, "dom", json.dumps(dom)[:80], "unexpected shape"))
        return []
    out: list[Pattern | None] = []
    for selector, spec in dom.items():
        if not isinstance(spec, dict):
            report.skipped.append(Skipped(name, "dom", selector, "unexpected shape"))
            continue
        for part, value in spec.items():
            if part in ("exists", "text"):
                out.append(parse_pattern(value, "dom", selector, part, name, report))
            elif part in ("attributes", "properties") and isinstance(value, dict):
                out += [
                    parse_pattern(v, "dom", selector, f"{part}.{attr}", name, report)
                    for attr, v in value.items()
                ]
            else:
                report.skipped.append(
                    Skipped(name, "dom", f"{selector} {part}", "unknown dom part")
                )
    return out


def _relations(name: str, rule: dict[str, Any], report: LoadReport) -> dict[str, tuple[Any, ...]]:
    def names(field_name: str) -> list[tuple[str, dict[str, str]]]:
        values = _as_list(rule.get(field_name, []))
        if values is None:
            report.skipped.append(
                Skipped(name, field_name, str(rule[field_name])[:80], "unexpected shape")
            )
            return []
        return [split_tags(v) for v in values]

    implied = []
    for target, attrs in names("implies"):
        try:
            confidence = int(attrs.get("confidence", 100))
        except ValueError:
            report.skipped.append(Skipped(name, "implies", target, "confidence is not a number"))
            continue
        implied.append(Implied(target, confidence, attrs.get("version", "")))
    categories = []
    for value, _ in names("requiresCategory"):
        try:
            categories.append(int(value))
        except ValueError:
            report.skipped.append(Skipped(name, "requiresCategory", value, "not a category id"))
    return {
        "implies": tuple(implied),
        "requires": tuple(v for v, _ in names("requires")),
        "excludes": tuple(v for v, _ in names("excludes")),
        "requires_category": tuple(categories),
    }


def parse_technology(
    name: str, rule: dict[str, Any], origin: str, report: LoadReport
) -> Technology:
    known = set(LIST_FIELDS) | set(DICT_FIELDS) | set(RELATION_FIELDS) | {"dom"}
    for field_name in rule:
        if field_name not in known | METADATA_FIELDS | REVIEW_FIELDS:
            report.ignored_fields[field_name] += 1
    return Technology(
        name=name,
        origin=origin,
        categories=tuple(int(c) for c in rule.get("cats", [])),
        website=rule.get("website"),
        patterns=tuple(_patterns(name, rule, report)),
        **_relations(name, rule, report),
    )


def _merge(old: Technology, new: Technology) -> Technology:
    """A custom or candidate entry for a known name adds to it; it never replaces it."""
    return replace(
        old,
        categories=old.categories + tuple(c for c in new.categories if c not in old.categories),
        patterns=old.patterns + new.patterns,
        implies=old.implies + new.implies,
        requires=old.requires + new.requires,
        requires_category=old.requires_category + new.requires_category,
        excludes=old.excludes + new.excludes,
    )


# ---------------------------------------------------------------- files


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Our own rule files: a broken one must stop the run, not silently drop rules.
        raise ValueError(f"{path}: cannot read rule file: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected an object of technology name -> rule")
    return data


def read_version_file(path: Path) -> dict[str, str]:
    """fingerprints/VERSION: "key: value" lines (source, commit, commit_date, copied)."""
    if not path.is_file():
        return {}
    pairs = (line.partition(":") for line in path.read_text(encoding="utf-8").splitlines())
    return {key.strip(): value.strip() for key, sep, value in pairs if sep}


def content_hash(rules: dict[str, Any]) -> str:
    """Hash of the rules' content, not their bytes: line endings and key order (CRLF on
    Windows, LF on Linux) must not change the rule-set version."""
    canonical = json.dumps(rules, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _load_folder(
    folder: Path,
    origin: str,
    technologies: dict[str, Technology],
    report: LoadReport,
    keep: Any = None,
) -> dict[str, Any]:
    """Load every *.json in `folder` into `technologies`; returns the raw rules used."""
    used: dict[str, Any] = {}
    for path in sorted(folder.glob("*.json")):
        report.files[origin] += 1
        for name, rule in _read_json(path).items():
            if keep is not None and not keep(name, rule):
                continue
            used[name] = rule
            tech = parse_technology(name, rule, origin, report)
            report.technologies[origin] += 1
            if name in technologies:
                report.merged.append(f"{name} ({origin})")
                technologies[name] = _merge(technologies[name], tech)
            else:
                technologies[name] = tech
    return used


def load_rules(rules_dir: Path, include_candidates: bool = False) -> RuleSet:
    """Load every rule under `rules_dir` (default layout: see the module docstring)."""
    upstream_dir = rules_dir / "webappanalyzer"
    report = LoadReport(
        upstream=read_version_file(rules_dir / "VERSION"), candidates_loaded=include_candidates
    )
    technologies: dict[str, Technology] = {}

    _load_folder(upstream_dir / "technologies", "webappanalyzer", technologies, report)

    def reviewed(name: str, rule: dict[str, Any]) -> bool:
        # Only rules a human reviewed reach the results (see candidates.promote);
        # anything else in custom/ is left out and listed, never counted silently.
        if rule.get("reviewed_by") == "human":
            return True
        report.unreviewed_custom.append(name)
        return False

    custom = _load_folder(rules_dir / "custom", "custom", technologies, report, keep=reviewed)
    version = f"webappanalyzer@{report.upstream.get('commit', 'unknown')[:12]}"
    version += f"+custom:{content_hash(custom) if custom else 'none'}"

    if include_candidates:

        def reviewable(name: str, rule: dict[str, Any]) -> bool:
            status = rule.get("status")
            if status in CANDIDATE_STATUSES:
                return True
            report.candidates_left_out[str(status)] += 1
            return False

        candidates = _load_folder(
            rules_dir / "candidates", "candidates", technologies, report, keep=reviewable
        )
        version += f"+candidates:{content_hash(candidates) if candidates else 'none'}"

    categories = {
        int(cat_id): Category(c["name"], tuple(c.get("groups", [])), int(c.get("priority", 0)))
        for cat_id, c in _read_json(upstream_dir / "categories.json").items()
    }
    groups_path = upstream_dir / "groups.json"
    groups = (
        {int(g_id): g["name"] for g_id, g in _read_json(groups_path).items()}
        if groups_path.is_file()
        else {}
    )
    report.distinct_technologies = len(technologies)
    _check_references(technologies, categories, report)
    return RuleSet(technologies, categories, groups, version, report)


def _check_references(
    technologies: dict[str, Technology], categories: dict[int, Category], report: LoadReport
) -> None:
    """List names and category ids that point nowhere: such a relation can never apply."""
    for tech in technologies.values():
        targets = [("implies", i.name) for i in tech.implies]
        targets += [("requires", r) for r in tech.requires]
        targets += [("excludes", e) for e in tech.excludes]
        for kind, target in targets:
            if target not in technologies:
                report.unknown_relations.append((tech.name, kind, target))
        for cat in (*tech.categories, *tech.requires_category):
            if cat not in categories:
                report.unknown_categories.append((tech.name, cat))

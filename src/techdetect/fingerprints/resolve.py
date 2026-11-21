"""Apply the relations between technologies: excludes, implies, requires.

This follows Wappalyzer's own code (wappalyzer.js and driver.js, npm wappalyzer@6.10.66)
in behaviour and order, with no extra layers:

  1. technologies with `requires` / `requiresCategory` are set aside: they count only
     once one of their required technologies, or a technology of a required category,
     is in the result (Wappalyzer analysed them only then);
  2. resolveExcludes: each technology removes the technologies it `excludes`;
  3. resolveImplies: each technology adds the technologies it `implies`, repeated until
     nothing new is added. An implied technology gets the lower of the implying
     technology's confidence and the `\\;confidence:` tag of the implies entry (default
     100), and the entry's `\\;version:` if any. A technology already in the result is
     never added again or changed: a direct detection stays as it is, and A <-> B loops
     end;
  4. if a set-aside technology's requirement is now met, it joins and steps 2-3 run
     again (Wappalyzer re-ran resolve() after analysing it).

Excludes run before implies, so an implied technology is never excluded.
"""

from dataclasses import dataclass, field

from techdetect.fingerprints.combine import TechnologyResult
from techdetect.fingerprints.loader import RuleSet, Technology


@dataclass(frozen=True)
class Resolution:
    technologies: dict[str, TechnologyResult]  # the final result, direct and inferred
    excluded: dict[str, str] = field(default_factory=dict)  # name -> excluded by
    # Matched, but none of its required technologies or categories was found: not kept.
    unmet_requirements: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _excludes(resolved: dict[str, TechnologyResult], ruleset: RuleSet) -> dict[str, str]:
    """resolveExcludes: remove every technology that a technology in the result excludes."""
    excluded: dict[str, str] = {}
    for name in list(resolved):
        if name not in resolved:
            continue  # already removed by an earlier technology: it excludes nothing
        for target in ruleset.technologies[name].excludes:
            if target != name and target in resolved:
                del resolved[target]
                excluded[target] = name
    return excluded


def _implies(resolved: dict[str, TechnologyResult], ruleset: RuleSet) -> None:
    """resolveImplies: add implied technologies until a pass adds nothing new."""
    added = True
    while added:
        added = False
        for name, result in list(resolved.items()):
            for implied in ruleset.technologies[name].implies:
                if implied.name in resolved or implied.name not in ruleset.technologies:
                    continue  # never added twice, never changes a technology already found
                resolved[implied.name] = TechnologyResult(
                    name=implied.name,
                    confidence=min(result.confidence, implied.confidence),
                    versions=(implied.version,) if implied.version else (),
                    tier="inferred",
                    groups=(),
                    implied_by=name,
                )
                added = True


def _requirements(tech: Technology) -> tuple[str, ...]:
    return (*tech.requires, *(f"category {c}" for c in tech.requires_category))


def _requirement_met(
    tech: Technology, resolved: dict[str, TechnologyResult], ruleset: RuleSet
) -> bool:
    """One required technology or category is enough, as in Wappalyzer, which analysed a
    technology as soon as any of the names or categories it requires was detected."""
    if any(name in resolved for name in tech.requires):
        return True
    categories = {c for name in resolved for c in ruleset.technologies[name].categories}
    return any(c in categories for c in tech.requires_category)


def resolve(direct: dict[str, TechnologyResult], ruleset: RuleSet) -> Resolution:
    """The final technologies of one domain, from its direct detections (combine())."""
    conditional = {
        name
        for name in direct
        if ruleset.technologies[name].requires or ruleset.technologies[name].requires_category
    }
    allowed = set(direct) - conditional
    while True:
        resolved = {name: result for name, result in direct.items() if name in allowed}
        excluded = _excludes(resolved, ruleset)
        _implies(resolved, ruleset)
        newly_met = {
            name
            for name in conditional - allowed
            if _requirement_met(ruleset.technologies[name], resolved, ruleset)
        }
        if not newly_met:
            unmet = {
                name: _requirements(ruleset.technologies[name])
                for name in sorted(conditional - allowed)
            }
            return Resolution(resolved, excluded, unmet)
        allowed |= newly_met

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from apps.contract_intelligence.domain.enums import DocumentType, Recommendation, Severity
from apps.contract_intelligence.domain.models import DecisionSummary, EvidenceRef, Finding, Obligation, ProjectDocumentRecord
from apps.contract_intelligence.ingestion.document_classifier import REQUIRED_BID_REVIEW_DOCUMENTS, missing_required_documents
from apps.contract_intelligence.ingestion.project_loader import ClauseSpan, LoadedDocument, iter_project_documents


SEVERITY_ORDER = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}

# Confidence score constants for finding and decision calculations
_SINGLE_EVIDENCE_CONFIDENCE = 0.68
_MULTI_EVIDENCE_CONFIDENCE = 0.78
_MAX_FINDING_CONFIDENCE = 0.90
_PUBLIC_OVERLAY_CONFIDENCE = 0.72
_NO_OVERLAY_CONFIDENCE = 0.56
_BASE_DECISION_CONFIDENCE = 0.84
_MISSING_DOC_PENALTY = 0.08
_UNREADABLE_DOC_PENALTY = 0.04
_HIGH_FINDING_PENALTY = 0.08
_MAX_UNREADABLE_PENALTY_COUNT = 3
_MIN_DECISION_CONFIDENCE = 0.35


@dataclass(frozen=True)
class FindingRule:
    role: str
    category: str
    severity: Severity
    title: str
    summary: str
    recommended_action: str
    patterns: tuple[str, ...]
    document_types: tuple[DocumentType, ...] = ()


@dataclass(frozen=True)
class BidReviewRunResult:
    project_id: str
    artifacts_dir: Path
    artifact_paths: dict[str, Path]
    decision_summary: DecisionSummary


CONTRACT_RISK_RULES: tuple[FindingRule, ...] = (
    FindingRule(
        role="contract_risk_analyst",
        category="payment_terms",
        severity=Severity.HIGH,
        title="Pay-if-paid structure shifts collection risk downstream",
        summary="Payment appears conditioned on owner payment to the prime contractor.",
        recommended_action="Seek pay-when-paid language or an outside payment deadline.",
        patterns=(r"pay[-\s]+if[-\s]+paid", r"conditioned upon.*receipt of payment"),
        document_types=(DocumentType.PRIME_CONTRACT, DocumentType.GENERAL_CONDITIONS, DocumentType.SPECIAL_PROVISIONS),
    ),
    FindingRule(
        role="contract_risk_analyst",
        category="indemnity",
        severity=Severity.HIGH,
        title="Broad indemnity language may exceed reasonable contractor risk",
        summary="The package appears to require expansive defense and indemnity obligations.",
        recommended_action="Seek negligence-based limits and remove duty-to-defend language where possible.",
        patterns=(r"defend,?\s+indemnify,?\s+and\s+hold harmless", r"indemnify.*whether.*caused in part"),
        document_types=(DocumentType.PRIME_CONTRACT, DocumentType.GENERAL_CONDITIONS, DocumentType.SPECIAL_PROVISIONS),
    ),
    FindingRule(
        role="contract_risk_analyst",
        category="delay_exposure",
        severity=Severity.HIGH,
        title="No-damages-for-delay language compresses schedule recovery options",
        summary="Delay remedies appear limited to time extensions rather than monetary recovery.",
        recommended_action="Preserve compensation rights for owner-caused delay, disruption, or resequencing.",
        patterns=(r"no damages? for delay", r"sole remedy.*extension of time"),
        document_types=(DocumentType.PRIME_CONTRACT, DocumentType.GENERAL_CONDITIONS, DocumentType.SPECIAL_PROVISIONS),
    ),
    FindingRule(
        role="contract_risk_analyst",
        category="change_orders",
        severity=Severity.MEDIUM,
        title="Strict written change authorization may bar valid field-change recovery",
        summary="Compensation may be limited to changes approved in writing before work proceeds.",
        recommended_action="Allow written notice plus later pricing when urgent field direction occurs.",
        patterns=(r"written change order", r"no extra compensation unless authorized in writing"),
        document_types=(DocumentType.PRIME_CONTRACT, DocumentType.GENERAL_CONDITIONS, DocumentType.SPECIAL_PROVISIONS),
    ),
    FindingRule(
        role="contract_risk_analyst",
        category="termination",
        severity=Severity.MEDIUM,
        title="Termination-for-convenience rights may leave recovery ambiguous",
        summary="Owner termination rights appear broad and may limit contractor recovery to narrow cost buckets.",
        recommended_action="Clarify recovery for demobilization, committed costs, and reasonable overhead.",
        patterns=(r"termination for convenience",),
        document_types=(DocumentType.PRIME_CONTRACT, DocumentType.GENERAL_CONDITIONS, DocumentType.SPECIAL_PROVISIONS),
    ),
)


INSURANCE_RULES: tuple[FindingRule, ...] = (
    FindingRule(
        role="insurance_requirements_analyst",
        category="additional_insured",
        severity=Severity.HIGH,
        title="Additional-insured requirement needs broker review",
        summary="The package appears to require owner-side additional-insured coverage language.",
        recommended_action="Confirm endorsement form and limit the requirement to ongoing/completed operations as appropriate.",
        patterns=(r"additional insured",),
        document_types=(DocumentType.INSURANCE_REQUIREMENTS, DocumentType.SPECIAL_PROVISIONS, DocumentType.PRIME_CONTRACT),
    ),
    FindingRule(
        role="insurance_requirements_analyst",
        category="waiver_subrogation",
        severity=Severity.MEDIUM,
        title="Waiver-of-subrogation language may exceed current program assumptions",
        summary="The package includes waiver-of-subrogation requirements that can affect program cost and claims posture.",
        recommended_action="Confirm carrier availability and price impact before bid submission.",
        patterns=(r"waiver of subrogation",),
        document_types=(DocumentType.INSURANCE_REQUIREMENTS, DocumentType.SPECIAL_PROVISIONS),
    ),
    FindingRule(
        role="insurance_requirements_analyst",
        category="primary_noncontributory",
        severity=Severity.MEDIUM,
        title="Primary and noncontributory wording should be confirmed against available endorsements",
        summary="The insurance stack appears to require primary/noncontributory positioning.",
        recommended_action="Confirm exact endorsement wording and whether it is available on required lines.",
        patterns=(r"primary and noncontributory",),
        document_types=(DocumentType.INSURANCE_REQUIREMENTS, DocumentType.SPECIAL_PROVISIONS),
    ),
    FindingRule(
        role="insurance_requirements_analyst",
        category="completed_operations",
        severity=Severity.MEDIUM,
        title="Completed-operations duration may carry longer-tail cost than expected",
        summary="The package appears to impose completed-operations coverage requirements after project completion.",
        recommended_action="Confirm duration, limits, and compatibility with current carrier terms.",
        patterns=(r"completed operations",),
        document_types=(DocumentType.INSURANCE_REQUIREMENTS, DocumentType.SPECIAL_PROVISIONS),
    ),
)


COMPLIANCE_RULES: tuple[FindingRule, ...] = (
    FindingRule(
        role="funding_compliance_analyst",
        category="davis_bacon",
        severity=Severity.HIGH,
        title="Davis-Bacon obligations likely apply",
        summary="The package references federal wage requirements that can materially affect payroll administration.",
        recommended_action="Confirm wage determinations, certified payroll workflow, and subcontractor compliance readiness.",
        patterns=(r"davis[-\s]+bacon", r"prevailing wage"),
        document_types=(DocumentType.FUNDING_DOCUMENT, DocumentType.PROCUREMENT_DOCUMENT, DocumentType.SPECIAL_PROVISIONS),
    ),
    FindingRule(
        role="funding_compliance_analyst",
        category="certified_payroll",
        severity=Severity.MEDIUM,
        title="Certified payroll administration appears required",
        summary="The package appears to require recurring payroll reporting.",
        recommended_action="Assign payroll compliance ownership and confirm weekly reporting capability before bid submission.",
        patterns=(r"certified payroll",),
        document_types=(DocumentType.FUNDING_DOCUMENT, DocumentType.PROCUREMENT_DOCUMENT, DocumentType.SPECIAL_PROVISIONS),
    ),
    FindingRule(
        role="funding_compliance_analyst",
        category="buy_america",
        severity=Severity.MEDIUM,
        title="Domestic sourcing rules may constrain procurement flexibility",
        summary="The package appears to include Buy America or similar domestic content obligations.",
        recommended_action="Confirm affected materials, waiver path, and supplier certification process.",
        patterns=(r"buy america", r"buy american", r"domestic content"),
        document_types=(DocumentType.FUNDING_DOCUMENT, DocumentType.PROCUREMENT_DOCUMENT, DocumentType.SPECIAL_PROVISIONS),
    ),
    FindingRule(
        role="funding_compliance_analyst",
        category="dbe_participation",
        severity=Severity.MEDIUM,
        title="DBE participation and documentation may require bid-stage planning",
        summary="The package references disadvantaged-business participation or reporting requirements.",
        recommended_action="Confirm bid-stage documentation and post-award tracking expectations.",
        patterns=(r"\bdbe\b", r"disadvantaged business"),
        document_types=(DocumentType.FUNDING_DOCUMENT, DocumentType.PROCUREMENT_DOCUMENT, DocumentType.SPECIAL_PROVISIONS),
    ),
)


NOTICE_DEADLINE_PATTERN = re.compile(
    r"(?P<context>(?:notice|claim|request)[^.:\n]{0,80}?within\s+(?P<days>\d+)\s+(?P<unit>business|calendar)?\s*days)",
    re.IGNORECASE,
)


def _project_id(project_dir: Path) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", project_dir.name.lower()).strip("-")
    return clean or "contract-project"


def _clause_evidence(document: LoadedDocument, clause: ClauseSpan, match: re.Match[str]) -> EvidenceRef:
    clause_text = clause.text
    start = max(0, match.start() - 100)
    end = min(len(clause_text), match.end() + 100)
    excerpt = " ".join(clause_text[start:end].split())
    return EvidenceRef(
        document_id=document.document_id,
        location=clause.location,
        excerpt=excerpt,
    )


def _finding_from_rule(rule: FindingRule, documents: list[LoadedDocument]) -> Finding | None:
    evidence: list[EvidenceRef] = []
    for document in documents:
        if rule.document_types and document.document_type not in rule.document_types:
            continue
        if not document.text_available:
            continue
        clauses = document.clauses or ()
        if not clauses:
            continue
        for clause in clauses:
            for pattern in rule.patterns:
                match = re.search(pattern, clause.text, flags=re.IGNORECASE)
                if match:
                    evidence.append(_clause_evidence(document, clause, match))
                    break
            if evidence:
                break
    if not evidence:
        return None

    confidence = _SINGLE_EVIDENCE_CONFIDENCE if len(evidence) == 1 else _MULTI_EVIDENCE_CONFIDENCE
    confidence = min(confidence, _MAX_FINDING_CONFIDENCE)
    return Finding(
        id=f"{rule.role}:{rule.category}",
        role=rule.role,
        category=rule.category,
        severity=rule.severity,
        title=rule.title,
        summary=rule.summary,
        recommended_action=rule.recommended_action,
        confidence=confidence,
        evidence=evidence[:3],
        uncertainty_notes=[],
    )


def _extract_obligations(documents: list[LoadedDocument]) -> list[Obligation]:
    obligations: list[Obligation] = []
    seen_titles: set[str] = set()

    for document in documents:
        if not document.text_available:
            continue

        for match in NOTICE_DEADLINE_PATTERN.finditer(document.text):
            clause = next((item for item in document.clauses if match.group("context") in item.text), None)
            days = match.group("days")
            unit = match.group("unit") or "calendar"
            title = f"Provide required notice within {days} {unit} days"
            if title in seen_titles:
                continue
            seen_titles.add(title)
            obligations.append(
                Obligation(
                    id=f"obl_notice_{len(obligations) + 1}",
                    source_clause=f"{document.relative_path}",
                    title=title,
                    obligation_type="notice_deadline",
                    trigger="contractual notice event",
                    due_rule=f"within {days} {unit} days",
                    owner_role="project_manager",
                    severity_if_missed=Severity.HIGH,
                    evidence=[_clause_evidence(document, clause, match)] if clause else [],
                )
            )

        certified_payroll = re.search(r"certified payroll", document.text, flags=re.IGNORECASE)
        if certified_payroll and "Submit certified payroll reports" not in seen_titles:
            clause = next((item for item in document.clauses if "certified payroll" in item.text.lower()), None)
            seen_titles.add("Submit certified payroll reports")
            obligations.append(
                Obligation(
                    id=f"obl_compliance_{len(obligations) + 1}",
                    source_clause=document.relative_path,
                    title="Submit certified payroll reports",
                    obligation_type="recurring_reporting",
                    trigger="during covered work",
                    due_rule="weekly during covered work",
                    owner_role="payroll_compliance_manager",
                    severity_if_missed=Severity.HIGH,
                    evidence=[_clause_evidence(document, clause, certified_payroll)] if clause else [],
                )
            )

        certificates = re.search(r"certificate[s]? of insurance", document.text, flags=re.IGNORECASE)
        if certificates and "Provide certificates of insurance before starting work" not in seen_titles:
            clause = next((item for item in document.clauses if "certificate" in item.text.lower()), None)
            seen_titles.add("Provide certificates of insurance before starting work")
            obligations.append(
                Obligation(
                    id=f"obl_insurance_{len(obligations) + 1}",
                    source_clause=document.relative_path,
                    title="Provide certificates of insurance before starting work",
                    obligation_type="pre_start_requirement",
                    trigger="before mobilization or notice to proceed",
                    due_rule="before starting work",
                    owner_role="risk_manager",
                    severity_if_missed=Severity.HIGH,
                    evidence=[_clause_evidence(document, clause, certificates)] if clause else [],
                )
            )

    return obligations


def _relationship_strategy(
    documents: list[LoadedDocument],
    all_findings: list[Finding],
) -> dict[str, object]:
    has_public_overlay = any(
        document.document_type in {DocumentType.FUNDING_DOCUMENT, DocumentType.PROCUREMENT_DOCUMENT}
        for document in documents
    )
    has_addenda = any(document.document_type is DocumentType.ADDENDUM for document in documents)
    insurance_pressure = any(finding.role == "insurance_requirements_analyst" for finding in all_findings)

    sensitive_issues = [finding.title for finding in all_findings if SEVERITY_ORDER[finding.severity] >= SEVERITY_ORDER[Severity.HIGH]][:3]
    leverage_points: list[str] = []
    if has_addenda:
        leverage_points.append("Use pre-bid clarification and addendum channels to resolve ambiguous commercial terms before price lock.")
    if insurance_pressure:
        leverage_points.append("Push broker-to-broker on endorsement wording rather than arguing abstract insurance concepts in principal-only terms.")
    if has_public_overlay:
        leverage_points.append("Focus negotiation on commercial allocation and notice mechanics rather than statutory funding terms that are likely rigid.")

    posture = (
        "Expect limited flexibility on funding-driven or public-agency compliance terms; prioritize commercial allocation, notice windows, and insurable-risk cleanup."
        if has_public_overlay
        else "Commercial terms may be negotiable, but the current package still needs structured human review before a bid commitment."
    )
    confidence = _PUBLIC_OVERLAY_CONFIDENCE if has_public_overlay else _NO_OVERLAY_CONFIDENCE
    return {
        "negotiation_posture": posture,
        "sensitive_issues": sensitive_issues,
        "leverage_points": leverage_points,
        "confidence": confidence,
    }


def _review_challenges(
    *,
    missing_docs: list[DocumentType],
    unreadable_documents: list[LoadedDocument],
    findings: list[Finding],
) -> dict[str, object]:
    contradictions: list[str] = []
    if not findings:
        contradictions.append("No material findings were generated; verify that the supplied files contain extractable contract text.")

    missed_hazards = [
        f"Missing required bid-review input: {document_type.value}"
        for document_type in missing_docs
    ]
    missed_hazards.extend(
        f"Unreadable source file requires manual review: {document.relative_path}"
        for document in unreadable_documents
    )
    human_review_required = bool(missed_hazards) or any(
        SEVERITY_ORDER[finding.severity] >= SEVERITY_ORDER[Severity.HIGH] for finding in findings
    )
    return {
        "contradictions": contradictions,
        "missed_hazards": missed_hazards,
        "human_review_required": human_review_required,
    }


def _decision_summary(
    *,
    project_id: str,
    findings: list[Finding],
    missing_docs: list[DocumentType],
    unreadable_documents: list[LoadedDocument],
    review_challenges: dict[str, object],
) -> DecisionSummary:
    high_or_worse = [finding for finding in findings if SEVERITY_ORDER[finding.severity] >= SEVERITY_ORDER[Severity.HIGH]]
    max_severity = max((SEVERITY_ORDER[finding.severity] for finding in findings), default=SEVERITY_ORDER[Severity.LOW])
    overall_risk = next(
        severity for severity, score in SEVERITY_ORDER.items() if score == max(max_severity, SEVERITY_ORDER[Severity.HIGH] if missing_docs else max_severity)
    )

    confidence = _BASE_DECISION_CONFIDENCE
    confidence -= _MISSING_DOC_PENALTY * len(missing_docs)
    confidence -= _UNREADABLE_DOC_PENALTY * min(len(unreadable_documents), _MAX_UNREADABLE_PENALTY_COUNT)
    if high_or_worse:
        confidence -= _HIGH_FINDING_PENALTY
    confidence = max(_MIN_DECISION_CONFIDENCE, min(confidence, _MAX_FINDING_CONFIDENCE))

    human_review_required = bool(review_challenges.get("human_review_required")) or confidence < 0.75

    if any(finding.severity is Severity.CRITICAL for finding in findings) or len(missing_docs) >= 2:
        recommendation = Recommendation.NO_GO
    elif high_or_worse or missing_docs or human_review_required:
        recommendation = Recommendation.GO_WITH_CONDITIONS
    else:
        recommendation = Recommendation.GO

    top_reasons = [finding.title for finding in sorted(findings, key=lambda item: SEVERITY_ORDER[item.severity], reverse=True)[:3]]
    top_reasons.extend(
        f"Missing required document: {document_type.value}" for document_type in missing_docs[:2]
    )

    must_fix_before_bid = [finding.recommended_action for finding in high_or_worse[:4]]
    must_fix_before_bid.extend(
        f"Obtain and review the missing {document_type.value.replace('_', ' ')}."
        for document_type in missing_docs
    )

    return DecisionSummary(
        project_id=project_id,
        recommendation=recommendation,
        overall_risk=overall_risk,
        confidence=round(confidence, 2),
        top_reasons=top_reasons[:4],
        must_fix_before_bid=must_fix_before_bid[:6],
        human_review_required=human_review_required,
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_bid_review(project_dir: str | Path, artifacts_dir: str | Path | None = None) -> BidReviewRunResult:
    project_path = Path(project_dir).expanduser().resolve()
    output_dir = Path(artifacts_dir).expanduser().resolve() if artifacts_dir else project_path / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)

    project_id = _project_id(project_path)
    documents = iter_project_documents(project_path)
    missing_docs = missing_required_documents([document.document_type for document in documents])
    unreadable_documents = [document for document in documents if not document.text_available]

    document_inventory = {
        "project_id": project_id,
        "documents": [
            ProjectDocumentRecord(
                document_id=document.document_id,
                filename=document.relative_path,
                document_type=document.document_type.value,
                required_for_bid_review=document.document_type in REQUIRED_BID_REVIEW_DOCUMENTS,
                text_available=document.text_available,
                text_source=document.text_source,
                clause_count=len(document.clauses),
            ).model_dump()
            for document in documents
        ],
        "missing_required_documents": [document_type.value for document_type in missing_docs],
    }

    risk_findings = [finding for rule in CONTRACT_RISK_RULES if (finding := _finding_from_rule(rule, documents))]
    insurance_findings = [finding for rule in INSURANCE_RULES if (finding := _finding_from_rule(rule, documents))]
    compliance_findings = [finding for rule in COMPLIANCE_RULES if (finding := _finding_from_rule(rule, documents))]
    all_findings = [*risk_findings, *insurance_findings, *compliance_findings]
    relationship_strategy = _relationship_strategy(documents, all_findings)
    review_challenges = _review_challenges(
        missing_docs=missing_docs,
        unreadable_documents=unreadable_documents,
        findings=all_findings,
    )
    obligations = _extract_obligations(documents)
    decision_summary = _decision_summary(
        project_id=project_id,
        findings=all_findings,
        missing_docs=missing_docs,
        unreadable_documents=unreadable_documents,
        review_challenges=review_challenges,
    )

    artifact_payloads: dict[str, object] = {
        "document_inventory.json": document_inventory,
        "risk_findings.json": [finding.model_dump() for finding in risk_findings],
        "insurance_findings.json": [finding.model_dump() for finding in insurance_findings],
        "compliance_findings.json": [finding.model_dump() for finding in compliance_findings],
        "relationship_strategy.json": relationship_strategy,
        "review_challenges.json": review_challenges,
        "decision_summary.json": decision_summary.model_dump(),
        "obligations_register.json": [obligation.model_dump() for obligation in obligations],
    }

    artifact_paths: dict[str, Path] = {}
    for filename, payload in artifact_payloads.items():
        path = output_dir / filename
        _write_json(path, payload)
        artifact_paths[filename] = path

    return BidReviewRunResult(
        project_id=project_id,
        artifacts_dir=output_dir,
        artifact_paths=artifact_paths,
        decision_summary=decision_summary,
    )

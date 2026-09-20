from __future__ import annotations

from .engine import import_template


SECURITY_REVIEW = {
    "key": "information-security-review",
    "name": "Information Security Review",
    "description": "Configurable design review with parallel SME review.",
    "domain": "INFORMATION_SECURITY",
    "version": 1,
    "publish": True,
    "steps": [
        {"key": "intake", "name": "Complete intake", "type": "HUMAN_TASK", "stage": "Intake", "assignment_role": "REQUESTOR"},
        {"key": "categorize", "name": "Categorize and engage", "type": "DECISION", "stage": "Categorization & Engagement", "assignment_role": "REVIEW_COORDINATOR"},
        {"key": "design", "name": "Primary design review", "type": "HUMAN_TASK", "stage": "Design Review", "assignment_role": "PRIMARY_REVIEWER"},
        {"key": "fork_sme", "name": "Start SME reviews", "type": "FORK", "stage": "Design Review"},
        {"key": "identity_sme", "name": "Identity SME review", "type": "HUMAN_TASK", "stage": "Design Review", "assignment_role": "IDENTITY_SME"},
        {"key": "network_sme", "name": "Network SME review", "type": "HUMAN_TASK", "stage": "Design Review", "assignment_role": "NETWORK_SME"},
        {"key": "architecture_sme", "name": "Architecture SME review", "type": "HUMAN_TASK", "stage": "Design Review", "assignment_role": "ARCHITECTURE_SME"},
        {"key": "join_sme", "name": "All required SME reviews complete", "type": "JOIN", "stage": "Design Review", "join_rule": "ALL"},
        {"key": "consolidate", "name": "Consolidate design conclusion", "type": "DECISION", "stage": "Design Review", "assignment_role": "PRIMARY_REVIEWER"},
        {"key": "build", "name": "Implement reviewed design", "type": "HUMAN_TASK", "stage": "Build", "assignment_role": "ENGINEER"},
        {"key": "validate", "name": "Validate implementation", "type": "HUMAN_TASK", "stage": "Validate", "assignment_role": "VALIDATOR"},
        {"key": "end", "name": "End review", "type": "END", "stage": "End"},
    ],
    "transitions": [
        {"from_step": "intake", "to_step": "categorize"},
        {"from_step": "categorize", "to_step": "design"},
        {"from_step": "design", "to_step": "fork_sme"},
        {"from_step": "fork_sme", "to_step": "identity_sme", "condition": {"field": "identity_review", "op": "truthy"}},
        {"from_step": "fork_sme", "to_step": "network_sme", "condition": {"field": "network_review", "op": "truthy"}},
        {"from_step": "fork_sme", "to_step": "architecture_sme"},
        {"from_step": "identity_sme", "to_step": "join_sme", "condition": {"field": "identity_review", "op": "truthy"}},
        {"from_step": "network_sme", "to_step": "join_sme", "condition": {"field": "network_review", "op": "truthy"}},
        {"from_step": "architecture_sme", "to_step": "join_sme"},
        {"from_step": "join_sme", "to_step": "consolidate"},
        {"from_step": "consolidate", "to_step": "build"},
        {"from_step": "build", "to_step": "validate"},
        {"from_step": "validate", "to_step": "end"},
    ],
}


THREAT_MODEL = {
    "key": "threat-model-assessment",
    "name": "Threat Model Assessment",
    "description": "Identify assets and threats, review mitigations in parallel, then approve.",
    "domain": "THREAT_MODELING",
    "version": 1,
    "publish": True,
    "steps": [
        {"key": "scope", "name": "Define model scope", "type": "HUMAN_TASK", "stage": "Scope", "assignment_role": "REQUESTOR"},
        {"key": "model", "name": "Document data flows and threats", "type": "HUMAN_TASK", "stage": "Model", "assignment_role": "THREAT_MODELER"},
        {"key": "fork", "name": "Start reviews", "type": "FORK", "stage": "Review"},
        {"key": "appsec", "name": "Application security review", "type": "HUMAN_TASK", "stage": "Review", "assignment_role": "APPSEC_SME"},
        {"key": "privacy", "name": "Privacy review", "type": "HUMAN_TASK", "stage": "Review", "assignment_role": "PRIVACY_SME"},
        {"key": "join", "name": "Reviews complete", "type": "JOIN", "stage": "Review", "join_rule": "ALL"},
        {"key": "approve", "name": "Approve mitigations", "type": "DECISION", "stage": "Decision", "assignment_role": "SECURITY_ARCHITECT"},
        {"key": "end", "name": "Complete threat model", "type": "END", "stage": "End"},
    ],
    "transitions": [
        {"from_step": "scope", "to_step": "model"}, {"from_step": "model", "to_step": "fork"},
        {"from_step": "fork", "to_step": "appsec"}, {"from_step": "fork", "to_step": "privacy"},
        {"from_step": "appsec", "to_step": "join"}, {"from_step": "privacy", "to_step": "join"},
        {"from_step": "join", "to_step": "approve"}, {"from_step": "approve", "to_step": "end"},
    ],
}


def seed(db):
    for template in (SECURITY_REVIEW, THREAT_MODEL):
        exists = db.execute("SELECT 1 FROM workflow_definition WHERE key=?", (template["key"],)).fetchone()
        if not exists:
            import_template(db, template)


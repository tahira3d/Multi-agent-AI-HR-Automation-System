# Responsible AI and fairness

This is a decision-support prototype, not an autonomous hiring system or a certified bias-audited product. It is engineering documentation, not legal advice.

Scoring is restricted to job-related, quoted evidence. It must not use demographic data, delivery characteristics, names, addresses, family status, health, disability, nationality, or inferred traits. Human reviewers make every progression decision, and stored evidence, flags, overrides, reports, and downloads are auditable.

The fairness subsystem is isolated from scoring. It suppresses groups smaller than five, reports selection-rate impact ratios, and creates findings below the 0.8 threshold. Counterfactual score deltas are available as a pure metric. Deployment owners must independently assess their legal duties, including the EU AI Act recruitment high-risk category, NYC Local Law 144, and EEOC disparate-impact guidance.

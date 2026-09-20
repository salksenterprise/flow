# Information Security Review integration example

This directory is a client example, not part of the workflow engine.

The external ISR application owns assessments, requirements, evidence, SME scope, conclusions, findings, and issue references. It starts a generic workflow using `business_type`, `business_key`, routing variables, and opaque subjects.

With the workflow service running:

```bash
python examples/information-security-review/client.py
```

The example creates a workflow linked to external assessment `ASMT-502`. No assessment or requirement record is stored in the workflow service.


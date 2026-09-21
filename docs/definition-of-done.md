# Definition of Done

Status: Draft for review.

These are gates, not aspirations. Where a gate can be checked by a machine it
is checked in CI and the build fails. Where it cannot, it is a review question
a reviewer must answer in writing before approving.

## Why these specific rules

Each rule below exists because its absence produced a defect listed in the
[requirements](requirements.md). This document is the residue of those defects,
not a generic checklist.

## Per change

A change is done when all of the following hold.

1. **It cites a requirement.** The change references a requirement identifier,
   or adds one. Work that fits no requirement is a signal to update the
   requirements first.
2. **A test names the requirement.** Every requirement the change moves to
   `Done` has at least one test citing its identifier. CI fails if the
   requirements document marks an identifier `Done` that no test cites.
3. **A defect fix begins with a failing test.** The commit history shows the
   test failing before the fix. A fix without a reproducing test is not done.
4. **Both sides of every authorization branch are tested.** A transition
   declaring a required permission has a test for the permitted actor and a
   test for the refused actor. This rule alone would have caught `EXE-3`.
5. **Both sides of every guard are tested.** A conditional edge has a test for
   the branch taken and the branch not taken.
6. **State survives a restart.** Any change touching schema, migration, or
   state derivation includes a test that runs the initialization path a second
   time against a populated database and asserts that no workflow or node state
   changed. This rule would have caught `REL-10`.
7. **Concurrency is exercised where it applies.** Any change to claiming,
   leasing, connection handling, or transaction scope includes a test with
   concurrent callers.
8. **The core stays pure.** `workflow_core` imports nothing beyond the standard
   library and its own modules. CI asserts this.
9. **No secret is readable.** No interface added or changed returns a signing
   secret, credential, or token.
10. **Errors are typed.** New failure paths raise a declared Flow error, not a
    bare exception, so an embedding host can catch and roll back.
11. **Documentation matches behavior.** If the change alters behavior described
    in a document, the document changes in the same commit. Status columns are
    updated honestly, including downgrades.
12. **The full suite passes.** No skipped tests, no expected failures left
    unexplained.

## Per requirement

A requirement may be marked `Done` only when:

~~~text
the behavior is implemented
a test cites the requirement identifier
the failure path is tested, not only the success path
the behavior holds after a process restart
the behavior holds under concurrent callers, where concurrency is possible
any limitation is stated in the requirement row, or the row reads Partial
~~~

A requirement whose implementation has a known limitation is `Partial` with the
limitation named. It is not `Done`.

## Per release

1. Every requirement is `Done`, `Partial` with a stated limitation, or
   explicitly deferred with a reason. No requirement is silently `Planned`.
2. No open `Defect` row.
3. The repository contract suite passes against every supported adapter,
   including the production database, not only SQLite.
4. A reference host application embeds the release and runs its own test suite
   green with no container, no network, and no background process.
5. Upgrade from the previous release is exercised against a populated database
   with in-flight workflows, and no in-flight workflow changes state.
6. The integration guide is followed end to end by someone who did not write
   it, from install to a first executing workflow.
7. Operational runbook exists for the failure modes the release introduces:
   stuck workflow, dead-lettered event, expired lease, failed migration.

## Not in the definition of done

Stated so that nobody treats their absence as an oversight:

~~~text
code coverage percentage targets
performance benchmarks, until a real workload exists
a second runtime implementation
graphical tooling
~~~

Coverage targets are excluded deliberately. The existing suite passes 22 tests
and missed a crash on every permissioned transition. Coverage would have been
high and wrong. Rules 4 through 7 above are the substitute.

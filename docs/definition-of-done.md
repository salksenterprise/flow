# Definition of Done

Status: Current.

These are gates, not aspirations. Where a gate can be checked by a machine it
is checked in CI and the build fails. Where it cannot, it is a review question
a reviewer must answer in writing before approving.

## Why these specific rules

Each rule below exists because its absence produced a defect during ISRP
orchestration review. This document is the residue of those defects, not a
generic checklist.

## Per change

A change is done when all of the following hold.

1. **It cites the design or delivery item it implements.** Work that fits no
   current ISRP design or delivery item is a signal to update the design first.
2. **A test names the invariant or defect.** New behavior has at least one test
   whose name or comment makes the protected rule discoverable. Historical
   requirement identifiers in existing tests remain useful trace labels, but
   they do not point to a second requirements specification.
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
8. **The orchestration stays self-contained.** `isrp.orchestration` imports
   nothing beyond the standard library and ISRP's own modules. CI asserts this.
9. **No secret is readable.** No interface added or changed returns a signing
   secret, credential, or token.
10. **Errors are typed.** New failure paths raise a declared ISRP orchestration
    error, not a bare exception, so the application can catch and roll back.
11. **Documentation matches behavior.** If the change alters behavior described
    in a document, the document changes in the same commit. Status columns are
    updated honestly, including downgrades.
12. **The full suite passes.** No skipped tests, no expected failures left
    unexplained.

## Per delivery item

A delivery-plan item may be marked `Done` only when:

~~~text
the behavior is implemented
a test names the acceptance criterion or protected invariant
the failure path is tested, not only the success path
the behavior holds after a process restart
the behavior holds under concurrent callers, where concurrency is possible
any limitation is stated beside the item, or the item reads Partial
~~~

A delivery item whose implementation has a known limitation is `Partial` with the
limitation named. It is not `Done`.

## Per release

1. Every committed delivery item is `Done`, `Partial` with a stated limitation,
   or explicitly deferred with a reason. No item is silently `Planned`.
2. No open `Defect` row.
3. The repository contract suite passes against every supported adapter,
   including the production database, not only SQLite.
4. The ISRP application runs its orchestration test suite green with no
   container, network, or background process.
5. Upgrade from the previous release is exercised against a populated database
   with in-flight requests and assessments, and none changes state.
6. The ISRP design and delivery instructions are followed end to end by someone
   who did not write them, from setup to a first executing request.
7. Operational runbook exists for the failure modes the release introduces:
   stuck request, dead-lettered event, expired lease, failed migration.

## Not in the definition of done

Stated so that nobody treats their absence as an oversight:

~~~text
code coverage percentage targets
performance benchmarks, until a real workload exists
a second runtime implementation
graphical tooling
~~~

Coverage targets are excluded deliberately. An earlier suite was green while
missing a crash on every permissioned transition. Coverage would have been high
and wrong. Rules 4 through 7 above are the substitute.

# Built-in builds execute their captured source

Canonical `BuildApplication`/built-in builder calls, five public View builders,
and candidate/required-View operations capture the installed package's actual
Python, JSON and required ZIP resources before computation. Dirty and untracked
package source is included. Available project metadata and dependency lock files
are captured with the Python implementation/version/platform identity. No
environment values, supplier clients, data roots or credential files are copied.
Source collection (including injected clients) retains its existing route; its
explicit Raw references and frozen configuration enter this build boundary.

`code_bundles/code-<digest>` contains one sealed content-addressed package. The
same content is reused across builds. A standard-library subprocess uses the
current Python executable, ignores checkout/PYTHONPATH imports, and imports that
package. This does not copy an environment or claim to vendor external dependencies.
An operation launches one process; nested domain/View builders share its context.
Custom `BuildExecutor` application protocols remain caller-defined; they are not
serialized as arbitrary Python programs.

`operations/RUN/frozen-canonical` and `frozen-views` retain the exact invocation and
code reference. Direct calls retain a content-addressed invocation under
`operations/frozen_calls`. Available Git HEAD/dirty diff is separate audit context;
actual captured bytes are authoritative and audit metadata does not affect code
identity. Credentials and hidden files are excluded from package capture.

An explicit run resumes its captured package even after the checkout changes.
Changing invocation inputs requires a new run ID. Completion records never bypass
ordinary input and artifact validation. Code or artifact corruption fails; it is
not repaired by overwriting immutable content. A restored root may relocate the
storage path while keeping all other references and invocation arguments fixed.

New canonical implementation references and View manifests bind `executed_code.v1`
(the code bundle and runtime). Their existing manifest configuration binds build
semantics. Old manifests without that reference retain their original interpretation;
no historical provenance is retrofitted. Preserve `code_bundles` and operation
invocations together with Raw, canonical, Snapshot and View artifacts when backing
up. The ordinary loaders validate referenced code payloads. Offline recovery
propagates its existing network/data deny guard to the frozen child, including
swallowed external-access attempts.

Manifest storage protocols with executed-code binding are canonical flat v3 /
partitioned v4, adjusted-price v2, market-replay v2, Qlib unadjusted v3 / adjusted
v4, financial Fact v6, and event Fact v5. Business projection policies and source
contracts are unchanged. Financial/event `code_bundle.json` now contains the
versioned code reference under these new protocols; their older protocols still
contain the original embedded source dictionary.

The isolated test evidence covers package Python and contract JSON changed while
a real builder is paused, caller configuration/current-pointer changes, frozen
resume, distinct changed-source runs, one process for five View kinds, direct-call
identity equivalence, corrupt source/data rejection, and a relocated offline root.
Tests using synthetic Readers or injected Python mocks exercise the internal
implementation boundary explicitly; those mocks do not represent captured source.

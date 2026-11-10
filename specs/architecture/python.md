# Python code conventions

## Goal of the document

This document describes language-level implementation and documentation conventions for Python code in the project.

## Scope

This specification covers project-wide conventions for Python language features, runtime constructs, and documentation in production code.
The following are out of scope:

- module ownership.
- domain behavior.
- entity modeling.
- error contracts.
- test organization.
- tool-managed formatting.

## Dictionary

- `project-controlled class` — a class whose instance layout is defined by project code rather than prescribed by external machinery or an inherited implementation contract.
- `instance dictionary` — the per-instance `__dict__` used to store dynamically named attributes.

## Runtime type validation

Functions called through project-controlled typed interfaces SHOULD treat annotated parameter types as caller contracts.
They SHOULD NOT perform runtime checks solely to verify that arguments match their declared types.
Runtime validation remains appropriate for untyped or external input and semantic constraints that annotations cannot express.

Code that constructs a semantically specific typed value from raw or untyped data MUST establish that type's semantic invariants at the construction boundary.
Functions and methods that receive the constructed typed value MUST assume those established invariants hold and MUST NOT repeat their validation.
This guarantee covers only invariants established by construction; relationships and external state still require the checks specified by their owning behavior contracts.

Semantic value modeling and data-entity validation MUST follow Semantic primitive types and Validation boundaries in `specs/architecture/entities.md`.

## Temporary implementation code

Production code intentionally introduced as a temporary bridge MUST include an adjacent `TODO` comment.
The comment MUST explain why the temporary code is necessary and identify the concrete condition, milestone, or tracked work item after which it MUST be removed.
Vague comments such as "remove later" are insufficient.
A separate tracked work item alone MUST NOT replace the adjacent explanation.
The comment MUST be removed together with the temporary code.

## Validation and resolution

Validation functions and methods MUST NOT return information incidentally obtained through any of these operations during validation:

- retrieval.
- resolution.
- transformation.
- extraction.

They MAY perform those operations internally when required to verify invariants and permitted by the owning validation boundary.
Callers that need the resulting information MUST obtain it through an operation whose explicit contract includes producing that information.

A validating constructor MAY return the object it constructs when construction is part of its explicit contract.
This exception MUST NOT be used to return information incidentally obtained during validation.
Framework validator hooks MAY return a validated value or instance when their framework requires that protocol.

Validation failure handling and result representation MUST follow Results and Callback boundaries in `specs/architecture/errors.md`.
Validation effects and ownership MUST follow Validation boundaries in `specs/architecture/entities.md` and Project state and operations in `specs/architecture/modules_layout.md`.

### Predicates

Predicates named `is_*`, `has_*`, or `can_*` SHOULD return `bool` and MUST NOT raise an exception for an ordinary negative result.

## Function body layout

Function and method bodies MUST use blank lines to separate distinct logical steps.
Statements that work together to perform one step SHOULD form a contiguous group.

**Example:** Validation, plan construction, and applying changes can form separate groups; preparing arguments and passing them to the same call belong together.

## Function extraction

A trivial collection update used at only one call site MUST remain inline and MUST NOT be extracted into a separate function merely to isolate the mutation.
An update is trivial when its intent is clear from the collection operations and it does not enforce a separate invariant or contain non-obvious control flow.
A separate function MAY be introduced when it serves at least one of these purposes:

- represent a meaningful operation.
- centralize an invariant.
- support reuse.
- simplify non-trivial control flow.

## Class instance layout

Explicit instance layouts keep attribute ownership predictable and prevent unintended instance dictionaries and dynamically named state.
New or substantially changed project-controlled classes MUST define `__slots__` explicitly unless an exception below applies.

A class that introduces instance attributes MUST list every attribute it introduces in `__slots__`.
A class that introduces no instance attributes MUST use `__slots__ = ()`.
Every covered subclass MUST define its own `__slots__`, including an empty declaration when it introduces no attributes.
A subclass MUST NOT repeat slot names owned by a base class.

Classes MUST NOT include `__dict__` in `__slots__` unless dynamically named instance attributes are an intentional part of the class contract.
Classes MUST include `__weakref__` only when instances need weak-reference support and no base class already provides it.

Existing classes SHOULD NOT be changed solely to adopt `__slots__`.
They MUST adopt this convention when their instance layout or implementation is substantially changed, unless an exception below applies.

### Exceptions

A class MAY omit an explicit `__slots__` declaration when one or more of the following conditions apply:

- a base class already provides an instance dictionary and preserving that inherited layout is required.
- external machinery or an inherited implementation contract owns the instance layout or requires dynamic attributes.
- dynamically named instance attributes are intentional behavior of the class.
- a concrete interoperability requirement is incompatible with a slotted instance layout.

Common examples include Pydantic models, exception classes, and protocols whose inherited machinery controls instance storage.
When the reason for omitting `__slots__` is not evident from the base class or implemented protocol, an adjacent comment MUST state the concrete reason.

## Docstrings

### Validation docstrings

Validation functions and methods MUST have docstrings describing the rule they enforce and at least one concrete example of an input or state with its expected outcome.
This requirement applies to model validators, guards, and validation predicates, including predicates that return a Boolean instead of raising or returning an error.

Examples SHOULD include both accepted and rejected cases when both are possible.
They SHOULD clarify non-obvious boundaries, such as prerequisite normalization, permitted missing values, and checks deferred to other layers.

Examples MAY use concise prose or code.
Executable doctests are not required.

**Example:** A destination validator's docstring can explain that `@/docs` and `@/docs/guide` conflict, while `@/docs` and `@/licenses` pass when neither overlaps a reserved project file.

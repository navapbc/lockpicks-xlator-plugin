# Legacy Lockpicks: Xlator (Translator) project

Goal: Represent and translate the given input (federal, state, and local government policy documents) into specs (intermediate representations of rulesets), following a Rules-as-Code (RaC) approach. To create the output, the specs are used to build the modernized system, quickly building much of the UI workflows to gather the input needed to run the ruleset.

## Key Principles

* Incremental Approach: Work on one program or rule set topic at a time, building confidence before expanding scope.
* Version Control: Commit specs after each logical milestone to track evolution and enable rollback.
* AI Collaboration: Use AI to accelerate translation, but human review ensures accuracy and policy compliance.
* Testing Focus: Comprehensive tests prevent regressions and document expected behavior.

### Ruleset as the codebase

This project treats the extracted ruleset (policies, workflows, decision logic, definitions) as a first-class code artifact rather than static documentation. Doing so provides the same rigor, traceability, and reliability we expect from software code.

#### Version Control & Traceability
- Every change is tracked and reviewable.
- Historical versions can be compared or restored.
- Rule evolution is transparent and auditable.

#### Code Review for Rule Changes
- Updates go through a PR review process.
- Logic changes are visible and discussable.
- Reduces silent drift and unintended consequences.

#### Tests Prevent Regressions
- Tests encode expected behavior and edge cases.
- Regressions are caught automatically.
- Behavior becomes executable documentation.
- Refactors are safer and verifiable.

#### Incremental Improvement
- New test cases are added as rules are added.
- Gaps surface via failing tests.
- Accuracy improves through small, controlled updates.

#### Automation & Product Readiness
- Supports validation, transpilation, and deployment.
- Potential integration with CI/CD.


### Desired Outcomes
Progress in Terms of Outcomes, Not Outputs (Outputs are activity. Outcomes are value.)

This rules extractor initiative:
- **Outcome**: Save meaningful time for project teams.
- **Leading indicator**: Accuracy of automatically generated rules.
- **Validation loop**: Early and frequent feedback from the AK team about what matters most.

## FAQ

- Is this testing functional feasibility?
  - Yes. This prototype tests if Claude Code can be used to extract *and maintain* the `specs` of a system (ruleset, workflows, documentation, etc.).
  - It treats the `specs` as code, so the specs are version-controlled and there are tests for the rulesets (and other parts of the `specs`) to ensure they behave as intended when run in a rules engine.
- Is this testing experiential value?
  - Yes. From this prototype, we'll learn can and can't be done with Claude Code, and how well an iterative approach works.
    The hypothesis is: *incrementally* building up the ruleset produces a more accurate and verifiable ruleset than generating and iterating on an entire ruleset.
- Is this testing look and feel?
  - No. User interface and visual design are not the focus of this prototype. However, the experience of interacting with the AI and the IDE (e.g., navigating files, hover-tiggered DSL documentation) will inform ideas about user experience and AI interaction patterns.
- Is this testing performance or scale?
  - Yes, it is testing extracted-ruleset accuracy and it will test performance as the policy/ruleset size grows.
- What is the primary (and secondary) purpose of this prototype?
  - Primary purpose: explore iterative approach of building and maintaining the `specs`. A desired outcome is that the `specs` are easier to build incrementally.
  - Secondary purpose: explore capabilities and limitations of Claude Code on a codebase of `specs` containing files in an atypical language.
- Are we building a foundation we can develop into a product?
  - Yes. The lessons learned (e.g., capabilities, incremental approach, test-driven validation of rules, transpilation/conversion to target languages) can inform the requirements and design of a product.
- Are we delivering a win to a client / project team?
  - TBD. We're testing initially with the Alaska team.
- Are we trying to prove or disprove a specific piece of the approach?
  - Yes, we are explicitly testing the hypothesis that *incrementally* building a ruleset results in a more accurate and verifiable outcome than generating a full ruleset and refining it afterward.
- Are we trying to demonstrate our approach for a potential / current client?
  - Yes, the prototype aims to demonstrate that an AI-assisted workflow can produce rulesets that are accurate, verifiable, and maintainable.
- Why Catala as the source spec format?
  - Catala is a domain-specific language designed for legal-policy specification, with exceptions, prioritized defaults, and module composition as first-class constructs. The AI authors `.catala_en` directly; SMEs read the Catala source alongside its literate-Markdown structure (`## Heading` sections mirror `policy_facets/computations/<rel>.md.yaml`, with inline `*Source: ...*` italic-prose citations).
  - Multi-target output is preserved structurally: Catala compiles to C, Java, OCaml, Python, JS (via OCaml→JS), and DOCX explanations via `catala-explain`. Building specific backends is out of scope for this project; the option remains for downstream consumers.
  - Eliminates the multi-surface tax of a custom DSL: schema, validator, transpiler, evaluator, expression engine, and tests no longer need to track every new policy idiom in lockstep — `clerk typecheck` and `clerk test` against the Catala source are authoritative.


## Step-by-Step Process

This project takes an incremental approach where each iteration involves the user (an SME on the policies) to perform the following:
- The user adds `input` docs and code in manageable-sized amounts of policy docs
    - The codebase contains the input and there is no context window (and hence no limit).
    - The AI searches the codebase for the data it needs (similar to RAG but without a vector DB).
- The user interacts with an AI to update the `specs`. Once satisfied, the specs are committed into git for version control.
    - The user interacts with the AI to create/update the specs (ruleset, workflows, etc.) in manageable amounts.
    - The specs are in a DSL format that will evolve over time.
    - The specs are machine-readable so that it can be used to build the UI workflows.
- Tests for updated specs are added by an AI and verified by the user to ensure future changes do not cause a regression.
- Once a logical set of rules are captured, the user guides the AI to generate `output`, including the ruleset and code to get end-user input and run the ruleset on a given rules engine.
    - A transpiler or converter may be needed to create the output ruleset so that it is usable by the modern system.

Each policy domain is a self-contained unit under `domains/<name>/`:

```
domains/
  ak_doh/                            ← example: Alaska DOH income eligibility
    input/policy_docs/               ← source policy documents (Markdown, PDF, etc.)
    specs/
      eligibility.catala_en          ← Catala source (human + AI authored)
      naming-manifest.yaml           ← canonical identifier names + per-field types
      tests/eligibility_tests.yaml   ← YAML test cases
    output/
      eligibility.catala_en          ← copy of source (for consumer build artifacts)
      eligibility_meta.py            ← field-category metadata sidecar
      tests/eligibility_tests.catala_en  ← Catala test file from YAML tests
      demo-catala-eligibility/
        main.py                      ← FastAPI backend
        static/index.html            ← browser form UI
        start.sh                     ← starts Catala-Python demo
  <next-domain>/                     ← add new domains here
    input/ specs/ output/

core/
  catala-authoring-quickref.md       ← AI-targeted Catala authoring reference
  catala-quickref.md                 ← general Catala-feature reference
tools/
  clerk_loop.py                      ← runs `clerk typecheck` + `clerk test`, parses diagnostics
  catala_eval.py                     ← thin `catala interpret --output-format=json` wrapper
  catala_depgraph.py                 ← computation-graph generator (Catala-native)
  catala_to_python.sh                ← Catala → Python transpiler (via clerk)
xlator.py                            ← CLI entry point (all pipeline actions)
xlator                               ← shell shim: exec uv run xlator.py "$@"
```

### How can I create a repo that uses the Xlator plugin?

The `download.sh` script downloads the Xlator repo template (and sets up a new git repository if an argument is provided).

In a terminal, use the `download.sh` script as follows:

```bash
curl -s https://raw.githubusercontent.com/navapbc/lockpicks-xlator-plugin/main/download.sh | bash -s -- [new_repo_path] [domains_subfolder_name]
```

If the arguments [new_repo_path] and [domains_subfolder_name]are provided, `create_git_repo.sh` is run to create a new repository at `new_repo_path` with a subfolder named `domains_subfolder_name` (defaults to 'domains').

If the arguments are not provided, the template will be downloaded and left in a folder named `xlator-repo-creator` for manual execution of `create_git_repo.sh`.

### 1. Input Collection
- Add policy documents to `domains/<name>/input/policy_docs/`
- Use AI (`/index-inputs`, `/refine-guidance`) to build the input index and guidance files

### 2. Spec Creation (AI-Assisted)
- `/extract-ruleset <domain>` — AI emits `domains/<name>/specs/<module>.catala_en` directly
- The skill drives `clerk typecheck` + `clerk test` after each emission and self-repairs before SME handoff (`xl-plugin/tools/clerk_loop.py`)
- Commit completed specs to version control

### 3. Test Definition (AI-Assisted)
- `/create-tests`, `/expand-tests` generate `domains/<name>/specs/tests/<module>_tests.yaml`
- Review and verify test scenarios; add edge cases and boundary conditions

### 4. Validation & Iteration
- `xlator catala-pipeline <domain> <module>` runs `clerk typecheck` + YAML→Catala test transpile + `clerk test`
- `xlator catala-demo <domain> <module>` starts the Catala-Python demo
- Iterate on specs as needed

### Example (AK DOH)

```bash
xlator list                                  # show all available domain/module pairs
xlator catala-pipeline ak_doh eligibility    # typecheck + test
xlator catala-demo ak_doh eligibility        # start Catala-Python demo at http://localhost:8000
```

See [README-dev.md](README-dev.md) for details.

## Vision diagram

```mermaid
flowchart TD

subgraph input
    policy_docs@{shape: docs} --> legacy_code
    legacy_code@{shape: procs}
    verified_artifacts
end

policy_docs --> Extractor1[[Extractor1]] --> ruleset
legacy_code --> Extractor2[[Extractor2]] --> specs

subgraph specs
    ruleset & workflows & artifacts
end

specs <--correct?--> verify[/verify/]

subgraph specs_testing
    tester_rule_engine[[Rule Engine]]
    ruleset --transpile?--> tester_rule_engine
    test_cases([test_cases]) --> tester_rule_engine --> expected_results([expected_results])
    tester_rule_engine --> explanation([explanation])
end

ruleset ---> Transpiler[[Transpiler]] --> ruleset2[ruleset]
workflows & artifacts ---> Coder[[Coder]] --> webforms

subgraph output["output (modern_system)"]
    ruleset2 --> rule_engine[[Rule Engine]] <--> code <--> webforms
end
```

- One incarnation of `Extractor1` is the [Policy Extraction (doc-to-logic) prototype](https://github.com/navapbc/lockpick-doc-to-logic)
- `Extractor2` will likely use AWS Transform, which also produces documentation, which would be included as part of the specs and can be used as input to the Coder.
    - Another option is to include verified output from AWS Transform (noted as `verified_artifacts`) as part of the `input`.

Not yet in the diagram:
- There can be multiple specs that can be compared to identify differences between systems (legacy vs legacy; modern vs modern; legacy vs modern).
- Validating the `modern_system` against the `legacy_system`

### Xlator implementation diagram

The following illustrates how Xlator currently implements the vision above for policy documents. The AI authors Catala source directly under `specs/`; the U2 clerk-loop runs `clerk typecheck` + `clerk test` after each AI emission and self-repairs before SME handoff.

See [README-dev.md](README-dev.md) for more detail.

```mermaid
flowchart TD

claude_domain(["Claude\n/new-domain"]):::claudeShape
claude_domain --> input
subgraph input
    policy_docs@{shape: docs}
end

policy_docs --> claude_index & claude_extract & claude_tests
subgraph Extractor
    claude_index(["Claude\n/index-inputs"]):::claudeShape
    claude_guidance(["Claude\n/refine-guidance"]):::claudeShape
    claude_extract(["Claude\n/extract-ruleset"]):::claudeShape
    claude_index --> index[input-index.yaml] --> claude_guidance
    claude_guidance --> ai_guidance[guidance]
    ai_guidance --> claude_extract
end

claude_extract --clerk_loop.py--> specs

subgraph specs
    catala_source["Catala source\n(.catala_en)"]
    naming_manifest["naming-manifest.yaml\n(identifiers + types)"]
    computation_graph["computation graph\n(.graph.yaml, .mmd)\n(via catala_depgraph.py)"]
end
specs <--correct?--> sme_verify[/SME verify/]:::smeShape

specs --> specs_tests
subgraph specs_tests
    claude_tests(["Claude\n/create-tests"]):::claudeShape
    claude_tests --> test_cases["test_cases\n(extracted, generated, and manual tests)"]
end
test_cases <--correct?--> sme_review[/SME review/]:::smeShape

catala_source ---> catala_pipeline

subgraph output["output"]
    catala_copy["Catala source copy\n(and *_meta.py)"]
    catala_pipeline(["xlator catala-pipeline"]):::toolShape
    catala_source --copy--> catala_copy
    catala_copy --> clerk_test

    subgraph catala_testing["output/tests"]
        test_cases --/catala-emit-tests--> tests_authored["specs/tests/*.catala_en"]
        tests_authored --copy--> catala_tests
        catala_tests --> clerk_test[["Catala: clerk test"]]:::toolShape
    end

    catala_copy --> catala_engine[["Catala Rule Engine"]]:::toolShape
    catala_copy --> claude_create_demo

    subgraph demo["output/demo-catala"]
        claude_create_demo(["Claude\n/create-demo"]):::claudeShape
        claude_create_demo --catala_to_python.sh--> python_files
        python_files --> demo_app["FastAPI demo\n(main.py + index.html)"]
        catala_engine <--> demo_app <--> webforms
    end

end

classDef claudeShape stroke:#FFAA00,stroke-width:6px;
classDef smeShape stroke:#AAFF00,stroke-width:6px;
classDef toolShape stroke:#00AAFF,stroke-width:6px;
```

# **Architecting Active Specification Alignment: Extending Code Tumbler with Agentic Playwright Verification**

## **1\. Introduction: The Agentic Verification Gap**

The paradigm of software development is undergoing a fundamental shift from human-centric authorship to agentic generation, characterized by systems like code-tumbler that employ autonomous loops of "See-Plan-Act" to produce software artifacts.1 In these architectures, the role of the human developer transitions to that of an "Architect," defining high-level intent, while "Digital FTEs" (Full-Time Employees) execute the implementation details.1 However, a critical vulnerability exists within the current generation of these systems: the "Verification Gap." While agents excel at generating syntactically correct code that passes unit tests, they frequently fail to verify *Active Specification Alignment*—the degree to which the running application’s observable behavior matches the holistic, often implicit, intent of the architectural plan.2  
Current verification strategies, as exemplified by the existing code-tumbler codebase, rely heavily on static analysis, linting, and standard unit testing frameworks (e.g., pytest, cargo test).1 These methods are "passive" in nature; they verify that the code compiles and that specific isolated functions return expected values. They do not, however, verify the "lived experience" of the software. A web application may pass every unit test yet fail to render a submit button on a mobile viewport, or a navigation flow may be technically functional but violate the user journey described in the requirements.4  
To bridge this gap, code-tumbler must be extended beyond passive code review to include "Active Verification." This involves integrating an agent capable of traversing the application layer—much like a human QA tester—to validate requirements dynamically. This report analyzes the integration of **Playwright** via the **Model Context Protocol (MCP)** and lightweight agentic wrappers like **browser-use** to create an "Active Verifier." By adopting the theoretical framework of "Specine" (Specification Alignment Engine) 2 and "Agentic Rubrics" 6, the system can implement a rigorous, self-correcting feedback loop that ensures generated software not only functions but aligns with the Architect's vision.

## **2\. Deconstructing the Code Tumbler Architecture**

To prescribe a robust extension strategy, one must first meticulously deconstruct the existing architecture of the code-tumbler system. The system functions as a sophisticated, file-driven state machine designed to coordinate the activities of specialized agents through iterative refinement cycles.

### **2.1 The Orchestration Hierarchy**

The core of the system is the **Orchestrator**, a file-watching daemon that manages the lifecycle of a project through a series of rigid state transitions.1 It monitors a projects/ directory for "trigger files," which serve as the signaling mechanism between agents. The workflow follows a strict linear progression:

1. **Trigger 1:** The user initiates the process by creating 01\_input/requirements.txt, which wakes the **Architect** agent.1  
2. **Trigger 2:** The Architect synthesizes these requirements into a technical blueprint, 02\_plan/PLAN.md, which triggers the **Engineer** agent.1  
3. **Trigger 3:** The Engineer produces the implementation in 03\_staging/ and signals completion via .manifest.json, triggering the **Verifier**.1  
4. **Trigger 4:** The Verifier generates a REPORT.md with a quality score. If the score is below the threshold (default 8.0), the Engineer is re-triggered; otherwise, the project is archived.1

This architecture reveals a crucial constraint: the **Verifier** is the gatekeeper. The quality of the final output is strictly bounded by the Verifier's ability to detect defects. If the Verifier is blind to UX issues or logic gaps that do not trigger compiler errors, the Orchestrator will unknowingly approve defective software.1

### **2.2 The Limits of the Current Verification Strategy**

The current verification strategy, detailed in VERIFICATION.md, is designed around a "Tumbling Cycle" that emphasizes isolation and determinism over behavioral analysis.1 The system spawns ephemeral Docker containers to execute four distinct phases: Install, Build, Test, and Lint. The scoring mechanism aggregates these signals into a composite score (0-10).1  
Table 1: Current Verification Scoring Model 1

| Component | Points | Methodology | Limitation |
| :---- | :---- | :---- | :---- |
| **Build Success** | 3 pts | Exit code 0 | Confirms syntax only; ignores logic. |
| **Test Pass Rate** | 4 pts | (passed / total) \* 4 | Relies on Engineer-written tests, which may be sparse or hallucinated. |
| **Linting** | 2 pts | Issue count \< 5 | Enforces style, not behavior. |
| **Critical Errors** | 1 pt | Log parsing | Catches panics, not subtle bugs. |
| **LLM Qualitative** | 0-10 pts | "Code Review" | Static text analysis; the LLM never "sees" the running app. |

The "Blind Spot" in this model is significant. The documentation admits that if the sandbox cannot run (e.g., unrecognized runtime), the system defaults to a "pure LLM code review".1 Even when the sandbox runs, it only executes what the Engineer wrote. If the Engineer forgets to write a test for the "Login" button, the Test Pass Rate remains high (100% of 0 tests is effectively passing in many naïve metrics), and the "Active Specification" (the requirement for a login button) is never verified. This aligns with the "Autonomous Digital FTE" concept 1, which demands higher autonomy, but current implementation lacks the "sensory" apparatus to achieve it. The "Problem History Records" (PHR) and "Architecture Decision Records" (ADR) mentioned in the governance documentation 1 provide a memory of past failures, but without active sensing, the system cannot verify if a new implementation repeats a past UI/UX mistake.

## **3\. Theoretical Framework: Active Specification Alignment**

To resolve the limitations of passive verification, code-tumbler must adopt the theoretical framework of **Active Specification Alignment**. This concept, derived from recent advances in software engineering research, posits that verification agents must actively probe the software environment to determine if the *perceived* specification (the reality of the running code) aligns with the *intended* specification (the Architect's plan).2

### **3.1 The "Specine" Methodology**

Research into the "Specine" (Specification Alignment Engine) methodology provides a rigorous three-stage process for agentic verification: **Identification**, **Lifting**, and **Alignment**.2  
**Identification of Misalignment:** The first challenge is detecting when the LLM-generated code diverges from the prompt. Standard execution-based evaluation (Pass@1) often fails to capture subtle misalignments where the code functions but does the wrong thing.3 For code-tumbler, this means the Verifier cannot simply run npm test. It must employ "Misaligned Specification Identification" by generating its own *independent* test cases derived strictly from the PLAN.md, distinct from the tests written by the Engineer.2  
**Specification Lifting:** This is the core transformative concept. "Specification Lifting" involves an agent observing the low-level execution of the code (e.g., the DOM state, network calls, accessibility tree) and "lifting" or reverse-engineering this back into a high-level requirement description.2 For example, instead of checking if button.id \== 'submit', the lifter agent observes: "There is a button labeled 'Submit' that, when clicked, sends a POST request to /api/login." This "Lifted Spec" is a text description of *what the software actually does*, generated independently of the source code's comments or intent.2  
**Alignment via Feedback:** The final step compares the "Lifted Spec" (Reality) against the "Input Spec" (Plan). Any discrepancy is a "Misalignment." This differs fundamentally from a bug report. A bug is a crash; a misalignment is a failure of intent (e.g., "The plan asked for a modal, but the app uses a redirect").2 This feedback loop is essential for the "Healer Agent" to perform accurate corrections.7

### **3.2 Agentic Rubrics and Execution-Free Verification**

While "Active Verification" is the gold standard, it is resource-intensive. Recent research introduces **Agentic Rubrics** as a complementary, lightweight mechanism.6 An Agentic Rubric is a structured checklist generated by an expert agent from the requirements *before* any code is written.  
In code-tumbler, the Architect is already generating a PLAN.md. An intermediate step should convert this Plan into a RUBRIC.yaml.6

* *Plan:* "User logs in via Supabase."  
* *Rubric Item:* "Check for supabase-js dependency." (Static)  
* *Rubric Item:* "Verify supabase.auth.signIn is called on form submission." (Static/Dynamic)

The integration of Agentic Rubrics provides a "context-grounded verifier" that guides the Engineer during generation and serves as a grading key for the Verifier, reducing the "vibe coding" randomness of LLM reviews.1

## **4\. The Engine of Active Verification: Playwright & MCP**

To implement Active Specification Alignment within code-tumbler, the system requires a sensor capable of interacting with the application layer. **Playwright** is the superior choice for this engine due to its architecture, token efficiency, and deep integration with modern LLM workflows via the **Model Context Protocol (MCP)**.

### **4.1 Comparative Analysis: Playwright vs. Alternatives**

While tools like Cypress, Selenium, and Puppeteer exist, Playwright's "out-of-process" architecture and Chrome DevTools Protocol (CDP) integration make it uniquely suited for agentic control.10  
Table 2: Verification Engine Capabilities for Agentic Systems

| Feature | Playwright | Cypress | Selenium | Relevance to Code Tumbler |
| :---- | :---- | :---- | :---- | :---- |
| **Architecture** | Out-of-process (CDP) | In-browser | WebDriver | CDP access allows agents to capture precise "Accessibility Trees" rather than raw DOM, saving tokens.12 |
| **Parallelism** | Native Contexts | Limited | Grid required | Essential for code-tumbler sandboxes to run multiple "User Journey" verifications simultaneously.11 |
| **Language Support** | Python, JS/TS,.NET | JS/TS only | Multi-language | code-tumbler uses a Python backend (verifier.py), making Playwright's native Python bindings a seamless fit.7 |
| **Agent Maturity** | High (MCP, Agents) | Low | Moderate | Playwright has established "Planner/Generator/Healer" patterns and official MCP support.7 |

Cypress runs inside the browser, which limits its ability to handle multi-tab flows or interact with browser-level events—capabilities often needed for complex verification.10 Selenium, while robust, lacks the modern "auto-wait" and trace viewer capabilities that are critical for debugging agent-generated tests without human intervention.5

### **4.2 The Model Context Protocol (MCP)**

The **Model Context Protocol (MCP)** represents a standardized interface between LLMs and tools, developed to prevent the "hallucination of API syntax" that plagues custom implementations.14 By integrating the playwright-mcp server, code-tumbler decouples the Verifier's "reasoning" from the "execution" details.  
**Mechanism of Action:**

1. The Verifier (LLM) decides it needs to "check the login page."  
2. Instead of writing a script, it emits an MCP tool call: call\_tool(name="navigate", args={"url": "http://localhost:3000"}).15  
3. The MCP Server receives this, executes the Playwright command, and returns a structured result.  
4. Crucially, the result is not a screenshot (which is heavy) or raw HTML (which is noisy), but often an **Accessibility Snapshot**—a token-efficient tree representation of the interactive elements (buttons, inputs).16

**Token Economics:** Standard HTML DOM dumps can exceed 27,000 tokens, overwhelming the context window of even large models. The Accessibility Tree utilized by Playwright MCP/Stagehand reduces this to \~6,000 tokens, a 78% reduction.17 This efficiency is vital for code-tumbler, which runs iteratively and tracks token usage costs in .tumbler/usage.json.1

### **4.3 The Three-Agent Pattern: Planner, Generator, Healer**

Integrating Playwright enables code-tumbler to adopt the "Three-Agent" pattern, a sophisticated workflow for resilient verification.7  
**1\. Planner Agent:** This agent consumes the PLAN.md and RUBRIC.yaml. Its sole job is to define "User Journeys"—high-level narratives of how a user interacts with the system (e.g., "User navigates to Home, clicks Pricing, verifies 'Pro Plan' exists"). It does not write code.7  
**2\. Generator Agent:** This agent translates the User Journeys into executable Playwright actions. It operates dynamically, choosing selectors based on the current page state. It utilizes "Auto-healing" logic: if \#submit-btn is missing, it looks for button.18  
**3\. Healer Agent:** When a verification step fails, the Healer Agent intervenes. It analyzes the Playwright Trace and DOM snapshot to determine if the failure is a "Test Defect" (the test is wrong) or a "Code Defect" (the app is broken). It generates the detailed feedback report that drives the next Engineer iteration.19

## **5\. Lightweight Solutions for Active Specification Alignment**

While a full-scale Playwright MCP implementation offers maximum robustness, the user query specifically requests "lightweight solutions." Full agentic loops can be slow and costly. We identify three distinct tiers of lightweight solutions suitable for code-tumbler.

### **5.1 Tier 1: The "Browser-Use" Library (Recommended)**

**Browser-use** is an open-source Python library designed to act as a lightweight, drop-in agentic wrapper for Playwright.20 It is the most immediate fit for code-tumbler's Python-based verifier.py.1

* **Mechanism:** It abstracts the complexity of context management and prompt engineering. The Verifier simply instantiates Agent(task="Verify login works", llm=model, browser=browser).20  
* **Performance:** It utilizes a specialized model ChatBrowserUse optimized for browser automation, claimed to be 3-5x faster than generic models.20  
* **Latency:** It runs "right next to the browser" in the same container, minimizing the network overhead seen with external MCP servers.20  
* **Context:** It automatically handles the "Accessibility Tree" extraction, ensuring the LLM only sees relevant interaction nodes.20

**Integration Strategy:** The verifier.py script in code-tumbler can import browser\_use. When the Sandbox launches the web server, the Verifier initializes a browser-use agent to run a "sanity check" derived from the plan. This requires minimal architectural changes compared to setting up a full MCP server.20

### **5.2 Tier 2: Stagehand & DOM Pruning**

For scenarios where browser-use is too high-level, or where fine-grained control is needed, **Stagehand** offers a "DOM Pruning" approach.17

* **The Problem:** Raw HTML is noisy. Scripts, metadata, and deeply nested divs confuse verification agents.  
* **The Solution:** Stagehand processes the DOM *before* sending it to the Verifier. It strips non-interactive elements and creates a simplified "Skeleton of Interaction".17  
* **Application:** This can be implemented as a lightweight utility script in the code-tumbler sandbox. Before the Verifier LLM reviews a page, the sandbox runs a "Pruning" script, ensuring the Verifier only pays for tokens that matter.

### **5.3 Tier 3: Playwright Labs "Best Practices" Generation**

The lightest weight solution is to avoid "active" agentic browsing during every run and instead use the **Generator Agent** to write a standard, high-quality Playwright test suite once.21

* **Workflow:**  
  1. Verifier reads PLAN.md.  
  2. Uses **Playwright Labs** best practices (fixtures, role-based locators) to generate tests/e2e/spec.ts.21  
  3. The Sandbox runs npx playwright test.  
* **Efficiency:** Subsequent iterations only run the test script (milliseconds/seconds) rather than invoking an LLM agent to browse the site (seconds/minutes \+ token costs).  
* **Robustness:** By using playwright-best-practices (e.g., "Use getByRole over CSS selectors"), the generated tests are less brittle and more aligned with user accessibility requirements.21

## **6\. Metrics and Governance: Measuring Alignment**

Implementing active verification tools provides the *capability* for alignment, but quantifying it requires a robust metrics framework. We leverage the "LLM-as-a-Judge" paradigm to create a governance layer for code-tumbler.

### **6.1 The Active Alignment Scorecard**

The existing 0-10 scorecard in VERIFICATION.md 1 is static. It must be expanded to include dynamic metrics derived from the active verification session.  
Table 3: Proposed Active Verification Metrics

| Metric Category | Weight | Measurement Methodology | Tooling |
| :---- | :---- | :---- | :---- |
| **Visual Fidelity** | 20% | Detection of layout shifts, overlapping elements, or "jank" during agent interaction. | Playwright Console Logs / Trace Viewer 22 |
| **Interactive Success** | 30% | Completion rate of "User Journeys" (e.g., Login \-\> Dashboard). | browser-use Task Completion Rate 20 |
| **Spec Completeness** | 30% | Percentage of RUBRIC.yaml items verified as "Present" and "Functional". | Agentic Rubric Grading 6 |
| **Resilience** | 10% | Absence of 500 errors, unhandled exceptions, or console errors during probing. | Playwright Network Interception 5 |
| **Accessibility** | 10% | Pass rate of automated a11y checks (e.g., axe-core injected via Playwright). | Playwright A11y Snapshots 16 |

### **6.2 LLM-as-a-Judge: DeepEval & Ragas**

To automate the scoring of these subjective metrics (e.g., "Is the dashboard layout 'clean'?"), code-tumbler can integrate **DeepEval** or **Ragas**.23

* **G-Eval Integration:** The **G-Eval** framework allows the definition of custom metrics using natural language. The Verifier can define a metric "Specification Adherence."  
* **Context:** The prompt includes the PLAN.md (Criteria), the RUBRIC.yaml (Checklist), and the Agent’s "Lifted Spec" (Observation).  
* **Reasoning:** G-Eval uses Chain-of-Thought (CoT) to reason: "The plan asked for a dark mode toggle. The agent's observation log shows no such button was found. Score: 0.8".26  
* **Output:** This generates a high-fidelity, explainable score that replaces the arbitrary "LLM Score" currently used in VERIFICATION.md.1

### **6.3 Governance: The Constitution**

The "Autonomous Digital FTE" concept introduces the need for a **Constitution**—a set of non-negotiable rules governing the agent's behavior.1 The Active Verifier acts as the judicial branch, enforcing this constitution.

* **Rule:** "All forms must have CSRF protection."  
* **Verification:** The Active Verifier attempts a replay attack using Playwright's network context. If it succeeds, the build fails, regardless of functionality.  
* **Rule:** "No hardcoded secrets."  
* **Verification:** The Verifier scans the "Lifted Spec" and network payloads for API keys.

## **7\. Architectural Migration Plan**

This section outlines the step-by-step engineering roadmap to transform code-tumbler from its current state to an Active Specification Alignment system.

### **Phase 1: Containerization & Sandbox Hardening**

The current sandbox mechanism must be upgraded to support headless browser execution.

* **Action:** Update the Docker image in sandbox.py to use mcr.microsoft.com/playwright:v1.40.0-jammy. This official image includes all system dependencies (GStreamer, codecs) required for browser execution.27  
* **Configuration:** Inject the necessary environment variables and flags for headless execution: \--headless=new, \--no-sandbox, \--disable-dev-shm-usage, \--disable-gpu.22 This ensures the browser runs reliably within the ephemeral container without crashing due to shared memory limits.

### **Phase 2: Integrating the "Browser-Use" Verifier**

We will modify the verifier.py agent to support the "Active Probe."

* **Dependency:** Add browser-use and playwright to the internal requirements.  
* **Logic:**  
  1. Detect if the project is a web application (presence of package.json, index.html, or React/Next.js references).  
  2. If Web: Start the application server in the background (e.g., npm run dev &).  
  3. Wait for port availability (e.g., localhost:3000).  
  4. Initialize browser\_use.Agent.  
  5. Feed PLAN.md to the Agent with the instruction: "Verify that the running application satisfies these requirements. Explore the UI and report discrepancies."  
  6. Capture the Agent's output (The "Lifted Spec") and screenshots.

### **Phase 3: The Feedback Loop (The "Healer")**

The feedback mechanism in 04\_feedback/REPORT.md must be enhanced.

* **Current:** Text-based report of lint errors.  
* **New:** A rich report containing "Visual Diff" data.  
* **Logic:** If the browser-use agent fails a task, it generates a "Reproduction Script" (a minimal Playwright snippet demonstrating the failure). This script is appended to the report. The Engineer agent is prompted to "Fix the code such that this script passes".19 This closes the loop with executable feedback rather than vague natural language complaints.

### **Phase 4: Risk Mitigation & Optimization**

* **Token Costs:** Active verification is token-heavy. Implement "DOM Pruning" (Stagehand) to strip the page before sending it to the Verifier.17 Limit the Verifier to checking only the *changed* features (incremental verification) rather than a full regression suite every time.  
* **Flakiness:** Implement "Auto-Wait" and "Self-Healing Selectors" in the Generator Agent to prevent false negatives caused by rendering timing.21 Use test.fixme() annotations for unstable tests to prevent them from blocking the pipeline while signaling the need for repair.

## **8\. Case Study: Verifying a Login Feature**

To illustrate the impact of this architecture, consider a standard requirement: "Create a login page with email and password."  
**Current Code Tumbler (Passive):**

1. **Engineer:** Writes LoginPage.js. Forget's the "Forgot Password" link.  
2. **Verifier:** Runs npm test. No tests exist for the link. Build passes.  
3. **Result:** "Success." (Specification Misalignment: The implied requirement of a usable login flow is unmet if the user is stuck).

**Extended Code Tumbler (Active):**

1. **Engineer:** Writes LoginPage.js. Forget's the "Forgot Password" link.  
2. **Planner:** Generates User Journey: "User goes to Login \-\> Clicks 'Forgot Password'."  
3. **Active Verifier:** Launches browser. Navigates to /login. Looks for "Forgot Password".  
4. **Observation:** "Link not found."  
5. **Lifting:** "The page contains Email and Password inputs but lacks recovery options."  
6. **Alignment:** "PLAN.md implies standard auth flow. Missing recovery option is a misalignment."  
7. **Result:** Failure. Feedback sent to Engineer: "Add Forgot Password link."

## **9\. Conclusion**

The extension of code-tumbler to support **Active Specification Alignment** represents a necessary evolution in agentic software engineering. By moving from passive code scanning to active, user-centric probing using **Playwright** and **MCP**, the system can verify not just the syntax of the code, but the *reality* of the application.  
The recommended path forward prioritizes the integration of **browser-use** as a lightweight, Python-native bridge to Playwright, supported by a containerized sandbox optimized for headless execution. This enables the implementation of **Specine's** "Lifting and Alignment" workflow, ensuring that the "Autonomous Digital FTE" produces software that is not only robust and error-free, but faithfully aligned with the Architect's intent. This shifts the code-tumbler from a code generator to a true software *builder*, capable of the nuanced, iterative refinement characteristic of expert human engineering.

#### **Works cited**

1. Implementing the Autonomous Digital FTE.md  
2. Aligning Requirement for Large Language Model's Code Generation \- arXiv, accessed February 9, 2026, [https://arxiv.org/html/2509.01313v1](https://arxiv.org/html/2509.01313v1)  
3. \[2509.01313\] Aligning Requirement for Large Language Model's Code Generation \- arXiv, accessed February 9, 2026, [https://arxiv.org/abs/2509.01313](https://arxiv.org/abs/2509.01313)  
4. Generating end-to-end tests with AI and Playwright MCP \- Checkly, accessed February 9, 2026, [https://www.checklyhq.com/blog/generate-end-to-end-tests-with-ai-and-playwright/](https://www.checklyhq.com/blog/generate-end-to-end-tests-with-ai-and-playwright/)  
5. The Complete Playwright End-to-End Story, Tools, AI, and Real-World Workflows, accessed February 9, 2026, [https://developer.microsoft.com/blog/the-complete-playwright-end-to-end-story-tools-ai-and-real-world-workflows](https://developer.microsoft.com/blog/the-complete-playwright-end-to-end-story-tools-ai-and-real-world-workflows)  
6. Agentic Rubrics as Contextual Verifiers for SWE Agents \- arXiv, accessed February 9, 2026, [https://arxiv.org/html/2601.04171v1](https://arxiv.org/html/2601.04171v1)  
7. Agentic AI Test Automation with Playwright \- Cegeka, accessed February 9, 2026, [https://www.cegeka.com/en/blogs/agentic-ai-test-automation-with-playwright](https://www.cegeka.com/en/blogs/agentic-ai-test-automation-with-playwright)  
8. Agentic Rubrics as Contextual Verifiers for SWE Agents \- arXiv, accessed February 9, 2026, [https://arxiv.org/pdf/2601.04171](https://arxiv.org/pdf/2601.04171)  
9. \[Quick Review\] SWE-Pruner: Self-Adaptive Context Pruning for Coding Agents \- Liner, accessed February 9, 2026, [https://liner.com/review/swepruner-selfadaptive-context-pruning-for-coding-agents](https://liner.com/review/swepruner-selfadaptive-context-pruning-for-coding-agents)  
10. Playwright vs Cypress: Key Differences, and When to Use Each | LambdaTest, accessed February 9, 2026, [https://www.lambdatest.com/blog/cypress-vs](https://www.lambdatest.com/blog/cypress-vs)  
11. Playwright vs Cypress: When to Use Each and Why \- DistantJob, accessed February 9, 2026, [https://distantjob.com/blog/playwright-vs-cypress/](https://distantjob.com/blog/playwright-vs-cypress/)  
12. Computer Use Agents in Modern Enterprise Architecture | by Vikas Gautam \- Medium, accessed February 9, 2026, [https://medium.com/p/32ae6b2b89ac](https://medium.com/p/32ae6b2b89ac)  
13. 8 Top Playwright Alternatives in 2026 for Smarter Testing \- TestGrid, accessed February 9, 2026, [https://testgrid.io/blog/playwright-alternatives/](https://testgrid.io/blog/playwright-alternatives/)  
14. Generative Automation Testing with Playwright MCP Server | by Andrey Enin \- Medium, accessed February 9, 2026, [https://adequatica.medium.com/generative-automation-testing-with-playwright-mcp-server-45e9b8f6f92a](https://adequatica.medium.com/generative-automation-testing-with-playwright-mcp-server-45e9b8f6f92a)  
15. Playwright Test Agents: AI Testing Explained | Bug0, accessed February 9, 2026, [https://bug0.com/blog/playwright-test-agents](https://bug0.com/blog/playwright-test-agents)  
16. microsoft/playwright-mcp: Playwright MCP server \- GitHub, accessed February 9, 2026, [https://github.com/microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp)  
17. What's the big deal about computer use? \- Browserbase, accessed February 9, 2026, [https://www.browserbase.com/blog/what-is-computer-use](https://www.browserbase.com/blog/what-is-computer-use)  
18. Modern E2E Testing with Playwright and AI \- YouTube, accessed February 9, 2026, [https://www.youtube.com/watch?v=emUaq9FPIcc](https://www.youtube.com/watch?v=emUaq9FPIcc)  
19. Playwright Agents: AI Plans, Writes & Fixes Your Tests Automatically\! \- YouTube, accessed February 9, 2026, [https://www.youtube.com/watch?v=Ok4QiO1iWMY](https://www.youtube.com/watch?v=Ok4QiO1iWMY)  
20. browser-use/browser-use: Make websites accessible for AI ... \- GitHub, accessed February 9, 2026, [https://github.com/browser-use/browser-use](https://github.com/browser-use/browser-use)  
21. Introducing Playwright Labs: Best Practices as Code \- DEV Community, accessed February 9, 2026, [https://dev.to/vitalicset/introducing-playwright-labs-best-practices-as-code-198n](https://dev.to/vitalicset/introducing-playwright-labs-best-practices-as-code-198n)  
22. Automated Performance Testing with Playwright and Chrome DevTools: A Deep Dive | by Aishah Sofea | Medium, accessed February 9, 2026, [https://medium.com/@aishahsofea/automated-performance-testing-with-playwright-and-chrome-devtools-a-deep-dive-52e8b240b00d](https://medium.com/@aishahsofea/automated-performance-testing-with-playwright-and-chrome-devtools-a-deep-dive-52e8b240b00d)  
23. LLM-as-a-judge: a complete guide to using LLMs for evaluations \- Evidently AI, accessed February 9, 2026, [https://www.evidentlyai.com/llm-guide/llm-as-a-judge](https://www.evidentlyai.com/llm-guide/llm-as-a-judge)  
24. Align an LLM as a Judge \- Ragas, accessed February 9, 2026, [https://docs.ragas.io/en/stable/howtos/applications/align-llm-as-judge/](https://docs.ragas.io/en/stable/howtos/applications/align-llm-as-judge/)  
25. Introduction to LLM Metrics | DeepEval by Confident AI \- The LLM Evaluation Framework, accessed February 9, 2026, [https://deepeval.com/docs/metrics-introduction](https://deepeval.com/docs/metrics-introduction)  
26. G-Eval | DeepEval by Confident AI \- The LLM Evaluation Framework, accessed February 9, 2026, [https://deepeval.com/docs/metrics-llm-evals](https://deepeval.com/docs/metrics-llm-evals)  
27. Mastering End-to-End Testing with Playwright and Docker \- BrowserStack, accessed February 9, 2026, [https://www.browserstack.com/guide/playwright-docker](https://www.browserstack.com/guide/playwright-docker)
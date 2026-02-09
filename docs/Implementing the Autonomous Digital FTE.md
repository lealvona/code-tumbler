# **Implementing the Autonomous Digital FTE:** 

## *A Comprehensive Architecture Guide*

# **Executive Summary**

The convergence of Large Language Models (LLMs), standardized context protocols, and declarative infrastructure has birthed a new paradigm in software engineering: the "Digital Full-Time Employee" (FTE). Unlike traditional coding assistants that operate as sophisticated autocomplete tools within an Integrated Development Environment (IDE), a Digital FTE is an autonomous agent capable of reasoning about system architecture, executing complex implementation plans, and maintaining production infrastructure with minimal human oversight.1 This report provides an exhaustive technical analysis and implementation guide for the architecture described in the "MASTER\_PROMPT.md" documentation, specifically drawing upon the "Code Tumbler" agentic framework, Spec-Driven Development (SDD), and the Model Context Protocol (MCP).2  
The system architecture analyzed herein represents a holistic ecosystem designed to support autonomous engineering. It moves beyond "vibe coding"—the unstructured, iterative prompting of AI—to a rigorous, constitution-based methodology where high-level specifications are deterministically transmuted into production-grade code.5 The technical stack supporting this methodology is equally specialized: Next.js 14 for the application layer, Supabase with strict Schema Isolation for data persistence, Drizzle ORM for type-safe database interactions, and a Kubernetes-native "Watcher" architecture that enables self-healing operations.2  
By synthesizing over 140 research artifacts, this report dissects the mechanisms required to implement this ecosystem. It explores the psychological priming of AI through the Master Prompt, the governance of autonomous code generation via Spec-Kit Plus, the architectural necessity of database schema isolation for multi-tenant AI contexts, and the security implications of sandboxing autonomous agents within GitOps pipelines.

# **1\. The Cognitive Kernel: The Master Prompt Architecture**

The foundation of any autonomous agent is its context. In the pre-agentic era, context was implicit—held in the minds of senior engineers or scattered across disparate documentation. In the agentic era, context must be explicit, machine-readable, and persistently available. The MASTER\_PROMPT.md file serves as this cognitive kernel, acting as the operating system for the AI agent.7

## **1.1 The Failure of Implicit Context and "Vibe Coding"**

Early attempts at AI integration in software development suffered from what industry analysts call "vibe coding." In this mode, developers treat the AI as a conversational partner, iteratively prompting it until the output feels correct.1 While effective for snippets, this approach fails at scale because it relies on the AI's ephemeral "vibe" or probabilistic next-token prediction without a grounded architectural truth.  
Research indicates that without a persistent definition of the project's reality, AI agents revert to generic training data. For example, when asked to modify a frontend component without a master prompt, an agent might introduce a generic state management library (like Redux) even if the project strictly enforces MobX, or use an incompatible version of a UI library, leading to "architectural drift".7 The Master Prompt solves this by pre-loading the agent's context window with a rigid definition of the universe it inhabits.

## **1.2 Anatomy of the MASTER\_PROMPT.md**

The MASTER\_PROMPT.md is not merely a README; it is a prompt engineering artifact designed to optimize the reasoning capabilities of LLMs (such as Claude 3.5 Sonnet or GPT-4). Analysis of the "Code Tumbler" repository and related "my-digital-twin" implementations reveals a hierarchical structure designed to maximize adherence to constraints.2

### **1.2.1 Identity and Role-Playing**

The prompt begins by establishing a strict persona. This is not for flavor, but for cognitive conditioning. By defining the agent as "Code Tumbler, an AI coding agent" or "A+, a creative technologist," the prompt restricts the model's solution space to professional, expert-level outputs.2 It suppresses the generic "helpful assistant" persona, which often hedges answers or provides overly verbose, novice-friendly explanations.

| Component | Function | Implementation Example |
| :---- | :---- | :---- |
| **Persona** | Defines expertise level and tone. | "You are Code Tumbler, a Senior SRE and GitOps specialist." |
| **Perspective** | Enforces consistency in communication. | "Always respond in the first person. Be concise." |
| **Trigger** | Defines invocation boundaries. | "Invoke via /code-tumbler in PR comments." |
| **Goal** | Sets the primary directive. | "Maintain stability of preview-environments." |

### **1.2.2 The Technical Constitution**

The most critical section of the Master Prompt is the technical constitution. This acts as a set of non-negotiable constraints that prevent technology sprawl. In the Code Tumbler architecture, this section explicitly mandates specific versions and libraries to ensure compatibility and maintainability.2

* **Framework Constraint:** "Next.js 14+ (App Router)." This prevents the agent from hallucinating solutions based on the older Pages router or mixing paradigms.  
* **Database Constraint:** "Supabase (PostgreSQL) with schema isolation." This dictates the data access pattern, preventing the agent from writing flat, unsecure SQL queries.  
* **ORM Constraint:** "Drizzle ORM." This forces type safety and prevents the use of incompatible ORMs like Prisma, which might be more prevalent in the model's training data but are disallowed in this specific architecture.2

## **1.3 Hierarchical Context and File Structure**

To function effectively, an agent must possess a mental map of the territory. The Master Prompt includes a textual representation of the repository structure (an ASCII tree) annotated with architectural intent.2 This technique, known as "Context Engineering," creates a U-shaped attention curve where the most critical information (the file map) is placed where the model is least likely to hallucinate.1  
The map details:

* **Application Logic:** /apps/web/src (Next.js source).  
* **Infrastructure:** /infrastructure/k8s (ArgoCD manifests).  
* **Data Layer:** /supabase/migrations (SQL definitions).  
* **Agent Instructions:** /code-tumbler/MASTER\_PROMPT.md (Recursive reference).

By explicitly mapping these directories, the Master Prompt reduces the token cost associated with file exploration. The agent does not need to run ls \-R to understand the project structure; it "knows" instinctively where a component should reside, significantly speeding up the "See-Plan-Act" loop.

## **1.4 Integration with "Constitution" Files**

Advanced implementations, such as those seen in the spec-kit and my-digital-twin ecosystems, extend the Master Prompt with a persistent constitution.md file.10 While the Master Prompt defines *what* the agent is, the Constitution defines *how* it must behave ethically and procedurally.  
This file typically resides in a hidden memory directory (e.g., .specify/memory/constitution.md) and contains high-level governing principles:

1. **Code Quality:** "All functions must be typed. No any types allowed."  
2. **Security:** "Never commit secrets. Always use environment variables."  
3. **User Experience:** "Optimize for Low Cumulative Layout Shift (CLS)."

The Master Prompt includes a directive to "Read the Constitution before generating any code," effectively creating a judicial review step within the agent's reasoning process.12

# **2\. Methodology: Spec-Driven Development (SDD)**

The Code Tumbler architecture explicitly rejects the "Vibe Coding" methodology in favor of Spec-Driven Development (SDD). SDD is an engineering paradigm that treats specifications not as ephemeral documentation, but as executable artifacts that drive the implementation process.3 This shift is essential for scaling autonomous agents, as it provides a verifiable contract between the human architect and the digital laborer.

## **2.1 The SDD Workflow Cycle**

Implementing SDD requires adherence to a strict four-phase cycle, orchestrated by the spec-kit or spec-kit-plus tooling.11 This cycle ensures that the agent moves from ambiguity to precision before a single line of code is written.

### **Phase 1: Specify (/speckit.specify)**

The workflow begins with the user providing a high-level intent via the /speckit.specify command. The goal is to capture the "what" and "why" of a feature without dictating the "how".3

* **Input:** "Create a system for audit logging."  
* **Agent Action:** The agent engages in a Socratic dialogue (using the /sp.clarify command from Spec-Kit Plus) to elicit hidden requirements.14 It asks about data retention policies, compliance requirements (GDPR/SOC2), and performance constraints.  
* **Output:** A spec.md file in the specs/ directory. This file contains user stories, functional requirements, and success criteria.

### **Phase 2: Plan (/speckit.plan)**

Once the specification is approved, the agent transitions to the planning phase. Here, the Master Prompt's constraints interact with the specification to generate a technical blueprint.

* **Architecture Decision:** The agent decides to use a new Supabase schema (audit\_log) rather than cluttering the public schema, adhering to the "Schema Isolation" rule.2  
* **Stack Selection:** It selects Drizzle ORM for the data access layer and Next.js Server Actions for the ingestion API, as mandated by the Tech Stack Constitution.  
* **Output:** A plan.md file detailing the database schema changes, API signatures, and component hierarchy.

### **Phase 3: Tasks (/speckit.tasks)**

The plan is then decomposed into atomic, verifiable units of work. This decomposition is critical for autonomous execution; an agent can easily get lost in a large refactor, but it can reliably execute a sequence of small tasks.3

* **Task Generation:** The agent parses the plan.md and generates a checklist.  
* **Granularity:** Tasks are sized to be completed in a single context window (e.g., "Create migration file," "Update Drizzle schema," "Create UI component").  
* **Output:** A tasks.md file or a GitHub Issue with a checklist.

### **Phase 4: Implement (/speckit.implement)**

Finally, the agent enters the execution loop. It iterates through the task list, implementing each item, running tests, and updating the checklist. This phase utilizes the "Code Tumbler Watcher" or a local agent runtime to modify files and run commands.13

## **2.2 Enterprise Governance: Spec-Kit Plus**

For enterprise-grade implementations, the basic SDD workflow is enhanced by spec-kit-plus features. This includes the curation of "Problem History Records" (PHR) and "Architecture Decision Records" (ADR).14

* **ADR Curation:** When the agent makes a significant architectural choice (e.g., "Use a separate schema for audit logs"), it documents this in an ADR. This creates a persistent memory of *why* decisions were made, preventing future agents from reversing them due to lack of context.  
* **PHR Tracking:** If an implementation fails (e.g., a migration lock timeout), the error and its resolution are recorded in a PHR. Future agents query this record before performing similar tasks, creating an "immune system" that learns from past failures.16

| Artifact | Purpose | Storage Location |
| :---- | :---- | :---- |
| **Spec** | Defines user requirements and business logic. | specs/ |
| **Plan** | Defines technical architecture and implementation steps. | plans/ |
| **Constitution** | Defines global rules and non-negotiables. | .specify/memory/ |
| **ADR** | Records architectural decisions and trade-offs. | .specify/memory/adrs/ |
| **PHR** | Records past failures and their solutions. | .specify/memory/phrs/ |

# **3\. The Runtime Stack: Next.js 14, Drizzle, and Supabase**

The Master Prompt mandates a specific, modern stack: **Next.js 14**, **Drizzle ORM**, and **Supabase**. This selection is not arbitrary; it is optimized for agentic development. These tools provide strong typing and explicit schemas, which serve as "guardrails" for AI code generation, reducing the likelihood of runtime errors.

## **3.1 Next.js 14 and the App Router**

The move to Next.js 14's App Router represents a shift towards a server-centric model that aligns well with AI reasoning. By defaulting to React Server Components (RSC), the architecture forces a clear separation between data fetching (server) and interactivity (client).2

* **Agentic Advantage:** The file-system based routing of the App Router gives the agent a predictable structure (page.tsx, layout.tsx, loading.tsx). The agent "knows" exactly where to place files to achieve a specific route structure without complex configuration.  
* **Server Actions:** The use of Server Actions for mutations eliminates the need for a separate API layer. The agent can write a function updateUser(data) and call it directly from a form, reducing the cognitive load required to manage REST endpoints or GraphQL resolvers.

## **3.2 Drizzle ORM: The Type-Safe Bridge**

Drizzle ORM is preferred over alternatives like Prisma in this architecture because of its lightweight, SQL-like syntax and superior schema management capabilities.2

* **Schema Declaration:** Drizzle allows defining schemas in TypeScript (schema.ts). This is crucial because the AI agent can read the TypeScript file to understand the database structure perfectly. It does not need to inspect a running database.  
* **Multi-Schema Support:** Drizzle has first-class support for PostgreSQL schemas via the pgSchema function.18 This is essential for the "Schema Isolation" requirement mandated by the Master Prompt.

TypeScript

// Implementation of Schema Isolation in Drizzle  
import { pgSchema, serial, text } from "drizzle-orm/pg-core";

// Define the private tenant schema  
export const tenantSchema \= pgSchema("tenant\_a");

// Define tables within that schema  
export const users \= tenantSchema.table("users", {  
  id: serial("id").primaryKey(),  
  email: text("email").notNull(),  
  role: text("role").default("user"),  
});

By explicitly defining schemas in code, the agent creates a rigid contract for data access. When the agent generates a query, Drizzle's TypeScript types ensure that the query matches the schema, allowing the IDE's compiler to catch hallucinations before the code is even run.19

## **3.3 Supabase and PostgREST Integration**

Supabase provides the backend infrastructure, utilizing PostgREST to expose the database as an API. The Master Prompt specifies "Supabase with schema isolation," which is a sophisticated security pattern.2

* **Pattern Definition:** In this pattern, the "public" schema (exposed by the API) contains only Views and Stored Procedures. The actual data resides in private schemas (e.g., app\_hidden, tenant\_123) that are inaccessible to the API directly.21  
* **Implementation:** The agent must script SQL migrations that:  
  1. Create the private schema (CREATE SCHEMA app\_hidden;).  
  2. Create the tables inside the private schema.  
  3. Create Views in the public schema that reference the private tables (CREATE VIEW public.users AS SELECT \* FROM app\_hidden.users;).  
  4. Grant permissions only on the Views.

This architecture decouples the public API from the internal data structure, allowing the AI agent to refactor the database without breaking external clients. It also provides a robust security layer, as no direct table access is ever exposed.22

# **4\. Database Architecture: Schema Isolation & Multi-Tenancy**

The requirement for "Schema Isolation" is a defining characteristic of the Code Tumbler architecture. It addresses the inherent risks of giving an autonomous agent access to a shared database. By compartmentalizing data, we limit the "blast radius" of any potential agent error.23

## **4.1 The Need for Isolation in Agentic Systems**

When an autonomous agent performs a migration or a data backfill, the risk of data corruption is non-zero. In a shared-schema multi-tenant application (where all tenants live in one table with a tenant\_id column), a single malformed UPDATE query could corrupt data across all customers.  
Schema Isolation mitigates this by assigning each tenant (or logical unit) their own PostgreSQL schema.

* **Security:** An agent operating on tenant\_a's schema literally cannot access tenant\_b's tables without explicitly switching context.  
* **Performance:** Queries are naturally scoped to smaller datasets, improving index performance.  
* **Manageability:** Agents can backup, restore, or migrate a single tenant independently of the others.

## **4.2 Implementing Schema Isolation with Drizzle and Supabase**

Implementing this requires careful orchestration between the application logic and the database DDL (Data Definition Language).

### **4.2.1 Dynamic Schema Management**

The application must dynamically select the correct schema based on the request context (e.g., a subdomain or header). The Code Tumbler architecture uses Drizzle's capabilities to handle this.

TypeScript

// Dynamic Schema Selection Helper  
import { pgSchema } from "drizzle-orm/pg-core";

// Function to generate schema object at runtime  
const getTenantSchema \= (schemaId: string) \=\> pgSchema(schemaId);

// Usage in a Server Action  
export async function getData(tenantId: string) {  
  const schema \= getTenantSchema(tenantId);  
  const usersTable \= schema.table("users", { /\* definition \*/ });  
    
  // Drizzle generates: SELECT \* FROM "tenant\_id"."users"  
  return await db.select().from(usersTable);  
}

### **4.2.2 The Migration Challenge**

Managing migrations across hundreds of schemas is a complex task. The "Code Tumbler Watcher" agent is tasked with this. The Master Prompt instructs the agent to treat migrations as iterative tasks.19

* **Migration Loop:** The agent does not run one migration command. It queries the list of active tenants, then loops through each one, applying the schema changes transactionally.  
* **Verification:** After each application, the agent runs a verification query to ensure the schema state matches the expected Drizzle definition.

## **4.3 PostgREST Configuration**

To support this isolation on the Supabase/PostgREST side, the configuration must be tuned.

* **db-schemas Setting:** PostgREST typically exposes one schema. To support multi-tenancy, it can be configured to switch schemas based on the Accept-Profile header, or (more commonly in Supabase) the application creates a wrapper in the public schema that uses set\_config('search\_path',...) to proxy requests to the correct internal schema.24

# **5\. Infrastructure as Code & The Code Tumbler Watcher**

The true autonomy of the system is realized in the "Code Tumbler Watcher" component. This is not merely a CI/CD script, but a persistent, self-healing entity residing within the Kubernetes cluster.6

## **5.1 The Watcher Architecture**

The Watcher is deployed as a Kubernetes **CronJob**. Unlike a Deployment which runs continuously, a CronJob spawns a fresh pod at defined intervals (e.g., every 5 minutes). This architectural choice is deliberate and crucial for AI reliability.

* **Context Reset:** By restarting the process frequently, the agent's memory (RAM) is wiped, preventing "context drift" or the accumulation of hallucinations that can occur in long-running AI sessions.  
* **Discrete Operations:** Each run is a discrete unit of work ("Check system health," "Apply pending migrations," "Triage new issues").

## **5.2 The Autonomous Loop: See, Plan, Act**

The Watcher implements a classic control loop, empowered by the LLM's reasoning.25

1. **See (Monitoring):** The Watcher pod starts. It uses the kubectl binary (via MCP or shell) to query the cluster state. It looks for pods in CrashLoopBackOff or Error states. It also queries the watcher.db (a SQLite database persisted on a volume) to check for unresolved incidents.6  
2. **Plan (Reasoning):** If an error is detected, the agent reads the logs (kubectl logs). It then constructs a prompt for Claude Code: "Pod X is failing with error Y. Based on the Master Prompt and Constitution, determine the fix."  
3. **Act (Remediation):**  
   * *Configuration Drift:* If the error is an environment variable or config map issue, the agent applies a kubectl patch.  
   * *Code Defect:* If the error is a bug (e.g., NullPointerException), the agent cannot fix it in production. Instead, it uses the GitHub MCP tool to create an Issue and a Draft PR with a proposed fix, effectively assigning the work to itself for the next development cycle.2  
4. **Remember (Logging):** The outcome is written to watcher.db. This creates a localized "knowledge base" of incidents, allowing the agent to recognize recurring patterns ("This service fails every Tuesday at 2 AM").

## **5.3 GitOps Integration with ArgoCD**

The Code Tumbler system relies on ArgoCD for deployment. This implies that the agent never directly modifies the running Deployment objects for application updates. Instead, it modifies the *manifests* in the Git repository.2

* **Preview Environments:** The Master Prompt describes a "Pull Request Generator" for ArgoCD. When the agent opens a PR (e.g., to fix the bug detected above), ArgoCD automatically detects the branch and spins up a temporary "Preview Environment" (a full namespace with the app and DB).  
* **Verification:** The agent then runs integration tests against this preview environment. Only if the tests pass does it request human review for the PR.

# **6\. The Model Context Protocol (MCP)**

The connective tissue of this entire ecosystem is the Model Context Protocol (MCP). MCP provides a standardized interface for the AI agent to interact with tools, data, and infrastructure.4

## **6.1 The "USB-C for AI"**

Prior to MCP, integrating an LLM with a tool like PostgreSQL or GitHub required custom "glue code" for each agent implementation. MCP standardizes this. The Code Tumbler agent acts as an **MCP Client**, connecting to various **MCP Servers**.

* **GitHub MCP Server:** Allows the agent to read issues, comment on PRs, and search code.27  
* **Postgres MCP Server:** Allows the agent to inspect the database schema and run read-only queries for debugging.  
* **Filesystem MCP Server:** Allows the agent to read and write files within the repository.

## **6.2 Building Custom MCP Servers**

To support the specific needs of the Code Tumbler Watcher, custom MCP servers are often required. For example, a "Kubernetes MCP Server" might expose tools like list\_pods, get\_logs, and restart\_deployment.28  
**Implementation Detail (Python/FastMCP):**  
A custom MCP server can be built using the fastmcp library to expose specific DevOps capabilities.

Python

from fastmcp import FastMCP  
import subprocess

\# Create an MCP server named "K8sOps"  
mcp \= FastMCP("K8sOps")

@mcp.tool()  
def get\_crashing\_pods(namespace: str) \-\> str:  
    """Returns a list of pods in CrashLoopBackOff state."""  
    cmd \= f"kubectl get pods \-n {namespace} \--field-selector=status.phase\!=Running"  
    result \= subprocess.run(cmd.split(), capture\_output=True, text=True)  
    return result.stdout

\# The agent connects to this server via stdio  
if \_\_name\_\_ \== "\_\_main\_\_":  
    mcp.run(transport="stdio")

This code snippet demonstrates how easily operational tools can be exposed to the agent. By wrapping kubectl commands in an MCP tool, we provide the agent with a structured, typed interface for infrastructure management, rather than forcing it to hallucinate shell commands.

## **6.3 Transport Mechanisms**

MCP supports two primary transports: stdio (Standard Input/Output) and sse (Server-Sent Events).29

* **Stdio:** Used for local agents (like the Code Tumbler Watcher running in a pod). The agent spawns the MCP server as a subprocess and communicates via pipes. This is secure and low-latency.  
* **SSE:** Used for remote agents. If the Code Tumbler agent were running in the cloud (e.g., on a developer's laptop) but needed to access K8s, it would connect to an MCP server running inside the cluster via an HTTP tunnel utilizing SSE.

# **7\. Security, Sandboxing, and Governance**

Granting an AI agent autonomy creates significant security risks. The Code Tumbler architecture employs a "Defense in Depth" strategy, utilizing sandboxing, RBAC, and constitutional governance.8

## **7.1 Sandboxing Execution**

The Code Tumbler Watcher executes code. To prevent accidental (or malicious) damage, strict sandboxing is enforced.

* **Container Isolation:** The Watcher runs in a dedicated Kubernetes namespace (code-tumbler-watcher) with Resource Quotas to prevent it from consuming all cluster CPU.25  
* **Read-Only Filesystem:** The container's root filesystem is mounted as Read-Only. The agent can only write to a specific ephemeral volume (/data or /scratch). This prevents the agent from modifying system binaries or persisting malware.  
* **gVisor / Kata Containers:** For high-security environments, the Master Prompt recommends running the agent pod using a secure runtime class like gVisor, which creates a kernel-level boundary between the agent and the host node.30

## **7.2 The "Human-in-the-Loop" Gate**

Despite its autonomy, the agent is not given "God Mode." Critical actions require human approval.

* **GitOps Gate:** The agent can push code to a branch, but it cannot merge to main. It must open a PR. The repository settings enforce "Require review from Code Owners."  
* **Destructive Action Gate:** The Master Prompt includes instructions that explicitly forbid destructive SQL commands (DROP TABLE, TRUNCATE) without a specific override flag (--force-destructive) which requires a human-signed token.

## **7.3 Auditability and Observability**

Every thought and action of the agent is logged.

* **Dashboard:** The Code Tumbler system includes a dashboard (Next.js app) that visualizes the watcher.db. It shows a timeline of "Incidents Detected" vs. "Fixes Applied".6  
* **Traceability:** Because the agent uses MCP, every tool call is logged. We can trace exactly *why* the agent decided to restart a pod (e.g., "Saw error 'Out of Memory' in logs \-\> Called restart\_pod tool").

# **8\. Conclusion**

The implementation of the system described in the MASTER\_PROMPT.md represents the cutting edge of modern software engineering. It creates a **Self-Driving Software Factory** where the human role shifts from laborer to architect.  
The synergy of **Spec-Driven Development** provides the rigorous "blueprints" necessary for AI coherence. The **Next.js/Supabase/Drizzle** stack offers the "pre-fabricated components" that ensure structural integrity. **MCP** serves as the universal "interface," allowing the digital workers to manipulate their tools with precision. Finally, the **Code Tumbler Watcher** infrastructure provides the "nervous system," enabling the software to sense its own health and repair itself.  
Organizations that successfully implement this architecture will not merely see incremental gains in productivity; they will achieve a fundamental change in velocity, enabling a small team of architects to manage a fleet of autonomous agents that maintain, refactor, and evolve complex distributed systems at a pace previously thought impossible.  
---

**References used in this analysis:**  
1

# **Works cited**

1. Part 1: General Agents: Foundations | Agent Factory, accessed February 6, 2026, [https://agentfactory.panaversity.org/docs/General-Agents-Foundations](https://agentfactory.panaversity.org/docs/General-Agents-Foundations)  
2. preview-environments/clopus/MASTER\_PROMPT.md at main \- GitHub, accessed February 6, 2026, [https://github.com/kubeden/preview-environments/blob/main/clopus/MASTER\_PROMPT.md](https://github.com/kubeden/preview-environments/blob/main/clopus/MASTER_PROMPT.md)  
3. Spec-driven development with AI: Get started with a new open source toolkit \- The GitHub Blog, accessed February 6, 2026, [https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/](https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/)  
4. Introducing the Model Context Protocol \\ Anthropic, accessed February 6, 2026, [https://anthropic.com/news/model-context-protocol](https://anthropic.com/news/model-context-protocol)  
5. Diving Into Spec-Driven Development With GitHub Spec Kit \- Microsoft for Developers, accessed February 6, 2026, [https://developer.microsoft.com/blog/spec-driven-development-spec-kit](https://developer.microsoft.com/blog/spec-driven-development-spec-kit)  
6. kubeden/clopus-watcher: An autonomous 24/7 on-call ... \- GitHub, accessed February 6, 2026, [https://github.com/kubeden/clopus-watcher](https://github.com/kubeden/clopus-watcher)  
7. Improve your AI code output with AGENTS.md (+ my best tips) \- Builder.io, accessed February 6, 2026, [https://www.builder.io/blog/agents-md](https://www.builder.io/blog/agents-md)  
8. Prompt for AGENTS.md file creating \- Discover gists · GitHub, accessed February 6, 2026, [https://gist.github.com/rodion-m/bb428bf45e3ff9ee094dda0bd8d748e1](https://gist.github.com/rodion-m/bb428bf45e3ff9ee094dda0bd8d748e1)  
9. my-digital-twin/master\_prompt.md at main \- GitHub, accessed February 6, 2026, [https://github.com/ammonhaggerty/my-digital-twin/blob/main/master\_prompt.md](https://github.com/ammonhaggerty/my-digital-twin/blob/main/master_prompt.md)  
10. Exploring spec-driven development with the new GitHub Spec Kit \- LogRocket Blog, accessed February 6, 2026, [https://blog.logrocket.com/github-spec-kit/](https://blog.logrocket.com/github-spec-kit/)  
11. AI Code Editor \- Tencent Cloud Code Assistant CodeBuddy, accessed February 6, 2026, [https://www.codebuddy.ai/blog/Spec-Kit](https://www.codebuddy.ai/blog/Spec-Kit)  
12. Installation and Setup \- The AI Agent Factory \- Panaversity, accessed February 6, 2026, [https://agentfactory.panaversity.org/docs/SDD-RI-Fundamentals/spec-kit-plus-hands-on/installation-and-setup](https://agentfactory.panaversity.org/docs/SDD-RI-Fundamentals/spec-kit-plus-hands-on/installation-and-setup)  
13. github/spec-kit: Toolkit to help you get started with Spec-Driven Development, accessed February 6, 2026, [https://github.com/github/spec-kit](https://github.com/github/spec-kit)  
14. panaversity/spec-kit-plus: A practical fork of github/spec-kit ... \- GitHub, accessed February 6, 2026, [https://github.com/panaversity/spec-kit-plus\#readme](https://github.com/panaversity/spec-kit-plus#readme)  
15. Spec-Kit Plus Foundation \- The AI Agent Factory \- Panaversity, accessed February 6, 2026, [https://agentfactory.panaversity.org/docs/SDD-RI-Fundamentals/spec-kit-plus-hands-on/spec-kit-plus-foundation](https://agentfactory.panaversity.org/docs/SDD-RI-Fundamentals/spec-kit-plus-hands-on/spec-kit-plus-foundation)  
16. From Ephemeral Code to Permanent Intelligence, accessed February 6, 2026, [https://pub-80f166e40b854371ac7b05053b435162.r2.dev/books/ai-native-dev/static/slides/chapter-14-slides.pdf](https://pub-80f166e40b854371ac7b05053b435162.r2.dev/books/ai-native-dev/static/slides/chapter-14-slides.pdf)  
17. PostgreSQL \- Drizzle ORM, accessed February 6, 2026, [https://orm.drizzle.team/docs/get-started-postgresql\#supabase](https://orm.drizzle.team/docs/get-started-postgresql#supabase)  
18. Schema \- Drizzle ORM, accessed February 6, 2026, [https://orm.drizzle.team/docs/sql-schema-declaration](https://orm.drizzle.team/docs/sql-schema-declaration)  
19. Schema-based Multi-Tenancy with Drizzle ORM | by vimulatus \- Medium, accessed February 6, 2026, [https://medium.com/@vimulatus/schema-based-multi-tenancy-with-drizzle-orm-6562483c9b03](https://medium.com/@vimulatus/schema-based-multi-tenancy-with-drizzle-orm-6562483c9b03)  
20. Supabase raises $200M Series D at $2B valuation | Hacker News, accessed February 6, 2026, [https://news.ycombinator.com/item?id=43763225](https://news.ycombinator.com/item?id=43763225)  
21. Schema Isolation — PostgREST 12.2 documentation, accessed February 6, 2026, [https://postgrest.org/en/v12/explanations/schema\_isolation.html](https://postgrest.org/en/v12/explanations/schema_isolation.html)  
22. Custom Schema and Exposing APIs \- Supabase \- Answer Overflow, accessed February 6, 2026, [https://www.answeroverflow.com/m/1329473373541109811](https://www.answeroverflow.com/m/1329473373541109811)  
23. Ask HN: Who wants to be hired? (February 2026\) \- Hacker News, accessed February 6, 2026, [https://news.ycombinator.com/item?id=46857487](https://news.ycombinator.com/item?id=46857487)  
24. Schemas — PostgREST 13.0 documentation, accessed February 6, 2026, [https://docs.postgrest.org/en/v13/references/api/schemas.html](https://docs.postgrest.org/en/v13/references/api/schemas.html)  
25. clopus-watcher/README.md at main \- GitHub, accessed February 6, 2026, [https://github.com/kubeden/clopus-watcher/blob/main/README.md](https://github.com/kubeden/clopus-watcher/blob/main/README.md)  
26. Code execution with MCP: building more efficient AI agents \- Anthropic, accessed February 6, 2026, [https://www.anthropic.com/engineering/code-execution-with-mcp](https://www.anthropic.com/engineering/code-execution-with-mcp)  
27. Using the GitHub MCP Server, accessed February 6, 2026, [https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp/use-the-github-mcp-server](https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp/use-the-github-mcp-server)  
28. Understanding MCP Stdio transport | by Laurent Kubaski \- Medium, accessed February 6, 2026, [https://medium.com/@laurentkubaski/understanding-mcp-stdio-transport-protocol-ae3d5daf64db](https://medium.com/@laurentkubaski/understanding-mcp-stdio-transport-protocol-ae3d5daf64db)  
29. Architecture overview \- Model Context Protocol, accessed February 6, 2026, [https://modelcontextprotocol.io/docs/learn/architecture](https://modelcontextprotocol.io/docs/learn/architecture)  
30. A field guide to sandboxes for AI \- Luis Cardoso, accessed February 6, 2026, [https://www.luiscardoso.dev/blog/sandboxes-for-ai](https://www.luiscardoso.dev/blog/sandboxes-for-ai)  
31. Whoaa512/starred \- GitHub, accessed February 6, 2026, [https://github.com/Whoaa512/starred](https://github.com/Whoaa512/starred)  
32. TheMacroeconomicDao/bybit-ai-trader: AI Trading Agent для Bybit через Cursor IDE | Knowledge Base 7.4k lines | Confluence-based analysis | Zero-risk methodology | 70%+ strategies | MCP Server integration \- GitHub, accessed February 6, 2026, [https://github.com/TheMacroeconomicDao/bybit-ai-trader](https://github.com/TheMacroeconomicDao/bybit-ai-trader)  
33. A Practical Guide to Spec-Driven Development \- Quickstart \- Zencoder Docs, accessed February 6, 2026, [https://docs.zencoder.ai/user-guides/tutorials/spec-driven-development-guide](https://docs.zencoder.ai/user-guides/tutorials/spec-driven-development-guide)  
34. Using Drizzle ORM with Supabase in Next.js: A Complete Guide \- MakerKit, accessed February 6, 2026, [https://makerkit.dev/blog/tutorials/drizzle-supabase](https://makerkit.dev/blog/tutorials/drizzle-supabase)  
35. Build an MCP client \- Model Context Protocol, accessed February 6, 2026, [https://modelcontextprotocol.io/docs/develop/build-client](https://modelcontextprotocol.io/docs/develop/build-client)  
36. Building Your Own Model Context Protocol (MCP) Server With Node and Python, accessed February 6, 2026, [https://www.coderslexicon.com/building-your-own-model-context-protocol-mcp-server-with-node-and-python/](https://www.coderslexicon.com/building-your-own-model-context-protocol-mcp-server-with-node-and-python/)  
37. Build Quality Software Faster with Spec Kit and Kilo Code | by Rizky Zulkarnaen \- Medium, accessed February 6, 2026, [https://medium.com/@ther12k/build-quality-software-faster-with-spec-kit-and-kilo-code-6b11019c1dcd](https://medium.com/@ther12k/build-quality-software-faster-with-spec-kit-and-kilo-code-6b11019c1dcd)  
38. panaversity/spec-kit-plus: A practical fork of github/spec-kit with patterns & templates for building scalable multi-agent AI systems. Ships production-ready stacks faster with OpenAI Agents SDK, MCP, A2A, Kubernetes, Dapr, and Ray. It also explicitly treats specifications, architecture history, prompt history, tests, and automated evaluations as first‑class artifacts., accessed February 6, 2026, [https://github.com/panaversity/spec-kit-plus](https://github.com/panaversity/spec-kit-plus)  
39. pg\_cron: Schedule Recurring Jobs with Cron Syntax in Postgres ..., accessed February 6, 2026, [https://supabase.com/docs/guides/database/extensions/pg\_cron](https://supabase.com/docs/guides/database/extensions/pg_cron)  
40. Build an MCP server \- Model Context Protocol, accessed February 6, 2026, [https://modelcontextprotocol.io/docs/develop/build-server\#testing-your-server-with-claude-for-desktop](https://modelcontextprotocol.io/docs/develop/build-server#testing-your-server-with-claude-for-desktop)
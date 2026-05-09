---
description: Append this chat’s completed work to project_data/task_report (title, description, human-time estimate, parent/subtask links).
---

You are invoked because the user ran **/log**.

## Goal

Update `project_data/task_report` so it records **what was just finished in this chat** (or the most recent coherent chunk of work if several things shipped—prefer one entry that groups them, or separate entries only if clearly unrelated).

## Steps

1. **Read** `project_data/task_report` if it exists (create the file and `project_data/` if missing).

2. **Infer from the conversation** (user requests, your edits, files touched):
   - A short **title** (imperative or outcome, e.g. “Add /log command and task report file”).
   - A **description** of what changed: behavior, files, and why (2–6 sentences).
   - A **human time estimate** if a competent developer did this **without** AI: give a range (e.g. `45–90 min`) or hours; assume familiarity with this repo unless the work was exploratory/research-heavy.

3. **Parent / subtask logic**
   - If this work **clearly continues or finishes** an item already described in `project_data/task_report`, treat it as a **subtask** (or completion) of that item:
     - Set **Parent** to the exact `##` heading title of that task (or the nearest existing parent).
     - Under that parent, add a bullet under a `### Subtasks` section (create it if missing), **or** add a short **Update** line under the parent noting what was completed.
   - If there is **no** suitable parent, use **Parent:** `—` and add a new top-level `##` section.

4. **Append** using this template (repeat `### Subtask` blocks if you logged multiple distinct subtasks under one parent):

```markdown
## YYYY-MM-DD — <Title>

- **Description:** <what was made / changed>
- **Human estimate (no AI):** <range>
- **Parent:** <parent ## title, or —>

### Subtasks (if any)
- <optional subtask titles completed in this chat>

---
```

5. **Do not** remove or rewrite old entries except to **add** `### Subtasks` / **Update** lines under an **existing** parent when linking this work as a subtask or completion.

6. **Save** the file and confirm to the user what you appended (title + file path).

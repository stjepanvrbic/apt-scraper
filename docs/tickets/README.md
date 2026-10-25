# Tickets

Each ticket is one Markdown file, `docs/tickets/<number>-<slug>.md`, numbered from `0001`. The front matter holds the fields that relate and order tickets, and no ticket repeats them in its body:

```yaml
---
title: Exact ticket title
type: Feature      # Feature, Bug, Chore or Decision
state: Todo        # Backlog, Todo, In Progress, Done or Canceled
priority: 3        # 1 Urgent, 2 High, 3 Medium, 4 Low, 0 None
assignee:          # empty until claimed
milestone:
parent:            # ticket number
blocked_by: []     # ticket numbers
related: []        # ticket numbers
---
```

A work ticket's body has the sections Outcome, Acceptance, Spec, Design, Deviations and Result. A decision's body has Question, Options, Decision, Reason and Consequences.

An umbrella is a ticket with children and no work of its own. Its `type` is empty, its body has Motivation, Outcome, Design, Spec and Exit criteria, and its last child, "Verify: <outcome>", is blocked by every sibling.

Comments, such as the reason a ticket was canceled, go in a final Comments section, each dated, oldest first.

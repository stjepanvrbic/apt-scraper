# Tickets

Each ticket is one Markdown file, `docs/tickets/<number>-<slug>.md`, numbered from `0001`. The body has the sections of the Task, Bug, Umbrella or Decision template. The front matter holds the fields that relate and order tickets, and no ticket repeats them in its body:

```yaml
---
title: Exact ticket title
type: Feature      # Feature, Bug, Decision or Chore. Umbrellas have none.
state: Todo        # Backlog, Todo, In Progress, Done or Canceled
priority: 3        # 1 Urgent, 2 High, 3 Medium, 4 Low, 0 None
assignee:          # empty until claimed
milestone:         # file name in docs/milestones/ without .md
parent:            # ticket number
blocked_by: []     # ticket numbers
related: []        # ticket numbers
---
```

A milestone is one file in `docs/milestones/` with an outcome sentence and at most five exit criteria. Membership comes from each ticket's `milestone` field.

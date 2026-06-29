# Planning method

Choose the smallest coherent sprint outcome whose acceptance can be proven. Include only issues required for that outcome. Record excluded nearby work so workers do not absorb it.

Parallelize only when modules, paths, interfaces, data, and runtime resources are independently owned. Serialize migrations, shared schema changes, and tightly coupled interface work unless a staged compatibility plan exists.

Each sprint criterion should map to one or more lane criteria and ultimately to evidence.

The sprint plan must also be reviewable by a stakeholder without reconstructing
hidden chat context. Record included work, deferred work, lane owner/reviewer,
QA milestones, human review milestones, and user stories for review inside
`sprint-plan.yaml`, not only in the final response.

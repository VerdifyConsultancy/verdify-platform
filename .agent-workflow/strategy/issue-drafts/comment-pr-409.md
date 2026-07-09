Closing as superseded; do not merge this branch wholesale.

The night-anchor/migration-177 portion conflicts with the approved dry-roots decision and was already reconciled live/source by migration 188. The July 9 review also rejects using this PR's deeper-DIF path as the current night-dry-out direction. Its `_common.py`/alerts changes overlap the new writer/evidence contracts.

The vision watchdog, k3s CronJob, runbook, and Frigate script work remain potentially useful and are preserved in the branch/commits. They should be extracted into a fresh vision-only issue/PR after the current recovery, with no migration-177, band-default, planner, or shared-ingestor overlap. No vision change is part of the approved software-recovery scope.

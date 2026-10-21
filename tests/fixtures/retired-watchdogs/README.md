# Retired watchdog templates

These exact pre-retirement definitions preserve migration-276 historical caller security fixtures. They are outside every Kustomize render and must not be deployed as an alternate alert path. Product #998 retires their two CronJobs and six dedicated grants only after monitoring #1016 replacement firing and both-collector rule loading pass. Existing SQL roles, credentials and migration checks remain; this archive is not authority to rotate or revoke them.
